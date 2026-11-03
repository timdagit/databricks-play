"""Gold transforms (spec section 3.3): one snapshot per virtual day, driven by the parameters.

Every function takes silver frames, an `as_of` date and the parameters it needs, and returns a
DataFrame with an `as_of_date` column. Nothing here reads or writes tables, so each function can
be tested on a few rows and re-run for any past day with that day's own parameter values.

Days are the virtual day a file was released (`_virtual_date`), not the transaction timestamp.
A late file therefore counts on the day it arrives and earlier snapshots are never revised.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

WINDOW_DAYS = 7  # trailing window for supplier concentration and review alerts
RECENT_DAYS = 3  # "recent" days compared with everything before them, for flags

# Keyword rule for a negative review. The real sample reviews are worded differently, so some
# would be missed. A sentiment model is out of scope for this PoC.
NEGATIVE_PATTERN = (
    r"\b(terrible|stale|rude|wrong|overpriced|disappointing|burnt|cold|never again)\b"
)


def _day() -> Column:
    return F.col("_virtual_date")


def _until(df: DataFrame, as_of: date) -> DataFrame:
    return df.filter(_day() <= F.lit(as_of))


def _window(df: DataFrame, as_of: date, days: int) -> DataFrame:
    start = as_of - timedelta(days=days - 1)
    return df.filter((_day() >= F.lit(start)) & (_day() <= F.lit(as_of)))


def _first_day(transactions: DataFrame, as_of: date) -> date:
    first = _until(transactions, as_of).agg(F.min(_day())).first()[0]
    return first or as_of


def _average(total: Column, days: int) -> Column:
    """`total` spread over `days`, or null when there are no days to average over."""
    return total / F.lit(days) if days else F.lit(None).cast("double")


def _stamp(df: DataFrame, as_of: date) -> DataFrame:
    return df.withColumn("as_of_date", F.lit(as_of)).select(
        "as_of_date", *[c for c in df.columns if c != "as_of_date"]
    )


def franchise_daily_kpis(
    transactions: DataFrame, as_of: date, promo_discount_rate: Decimal | float
) -> DataFrame:
    """The day's sales per franchise. `revenue_at_promo` is revenue if every price had the
    promo discount, with volume unchanged (the what-if notebook explores volume response)."""
    day = transactions.filter(_day() == F.lit(as_of))
    kpis = day.groupBy("franchiseID").agg(
        F.count(F.lit(1)).alias("transactions"),
        F.sum("quantity").alias("units"),
        F.sum("totalPrice").alias("revenue"),
        F.countDistinct("customerID").alias("unique_customers"),
    )
    kpis = kpis.withColumn("avg_basket", F.col("revenue") / F.col("transactions")).withColumn(
        "revenue_at_promo", F.col("revenue") * (1 - F.lit(float(promo_discount_rate)))
    )
    return _stamp(kpis, as_of)


def franchise_flags(
    transactions: DataFrame,
    as_of: date,
    underperform_pct: Decimal | float,
    min_txn_for_ranking: int,
) -> DataFrame:
    """Compare each franchise's average daily revenue over the last `RECENT_DAYS` days with
    all the days before. A franchise is only ranked, and only flagged, once the earlier days
    hold at least `min_txn_for_ranking` transactions."""
    first = _first_day(transactions, as_of)
    recent_start = as_of - timedelta(days=RECENT_DAYS - 1)
    recent_days = (as_of - max(first, recent_start)).days + 1
    earlier_days = max(0, (recent_start - first).days)
    is_recent = _day() >= F.lit(recent_start)
    revenue = F.col("totalPrice")

    totals = (
        _until(transactions, as_of)
        .groupBy("franchiseID")
        .agg(
            F.sum(F.when(is_recent, revenue).otherwise(0)).alias("recent_revenue"),
            F.sum(F.when(~is_recent, revenue).otherwise(0)).alias("earlier_revenue"),
            F.sum(F.when(~is_recent, 1).otherwise(0)).alias("earlier_txn_count"),
        )
    )
    recent_avg = F.col("recent_revenue") / F.lit(recent_days)
    earlier_avg = (
        F.col("earlier_revenue") / F.lit(earlier_days)
        if earlier_days
        else F.lit(None).cast("double")
    )
    flags = (
        totals.withColumn("recent_avg_revenue", recent_avg)
        .withColumn("earlier_avg_revenue", earlier_avg)
        .withColumn(
            "change_pct",
            F.when(
                F.col("earlier_avg_revenue") > 0,
                (F.col("recent_avg_revenue") - F.col("earlier_avg_revenue"))
                / F.col("earlier_avg_revenue"),
            ),
        )
        .withColumn("eligible", F.col("earlier_txn_count") >= F.lit(min_txn_for_ranking))
    )
    rank = F.dense_rank().over(
        Window.partitionBy("eligible").orderBy(F.col("recent_avg_revenue").desc())
    )
    flags = flags.withColumn("revenue_rank", F.when(F.col("eligible"), rank)).withColumn(
        "underperforming",
        F.col("eligible")
        & F.col("change_pct").isNotNull()
        & (F.col("change_pct") <= -F.lit(float(underperform_pct))),
    )
    return _stamp(
        flags.drop("recent_revenue", "earlier_revenue"),
        as_of,
    )


def customer_loyalty_tiers(
    transactions: DataFrame,
    as_of: date,
    gold_min_spend: Decimal | float,
    silver_min_spend: Decimal | float,
    lookback_days: int,
) -> DataFrame:
    """Spend per customer over the lookback window, and the tier it earns."""
    spend = (
        _window(transactions, as_of, lookback_days)
        .groupBy("customerID")
        .agg(F.sum("totalPrice").alias("spend"), F.count(F.lit(1)).alias("transactions"))
    )
    tier = (
        F.when(F.col("spend") >= F.lit(float(gold_min_spend)), "gold")
        .when(F.col("spend") >= F.lit(float(silver_min_spend)), "silver")
        .otherwise("bronze")
    )
    return _stamp(spend.withColumn("tier", tier), as_of)


def supplier_concentration(
    transactions: DataFrame,
    franchises: DataFrame,
    as_of: date,
    threshold: Decimal | float,
) -> DataFrame:
    """Share of the window's revenue sold through the franchises each supplier serves.

    `franchises` is the SCD2 silver table. The row in force on `as_of` gives each franchise's
    supplier, so a supplier change moves revenue from the day it took effect.
    """
    in_force = franchises.filter(
        (F.col("_valid_from") <= F.lit(as_of))
        & (F.col("_valid_to").isNull() | (F.col("_valid_to") > F.lit(as_of)))
    ).select("franchiseID", "supplierID")
    revenue = (
        _window(transactions, as_of, WINDOW_DAYS)
        .groupBy("franchiseID")
        .agg(F.sum("totalPrice").alias("revenue"))
    )
    by_supplier = (
        revenue.join(in_force, "franchiseID")
        .filter(F.col("supplierID").isNotNull())
        .groupBy("supplierID")
        .agg(F.sum("revenue").alias("revenue"), F.count(F.lit(1)).alias("franchises"))
    )
    total = F.sum("revenue").over(Window.partitionBy())
    shared = by_supplier.withColumn("revenue_share", F.col("revenue") / total).withColumn(
        "concentrated", F.col("revenue_share") > F.lit(float(threshold))
    )
    return _stamp(shared, as_of)


def review_alerts(reviews: DataFrame, as_of: date, min_count: int) -> DataFrame:
    """Negative reviews per franchise in the window, and whether they reach the alert level."""
    negative = F.lower(F.col("review")).rlike(NEGATIVE_PATTERN)
    counts = (
        _window(reviews, as_of, WINDOW_DAYS)
        .groupBy("franchiseID")
        .agg(
            F.count(F.lit(1)).alias("reviews"),
            F.sum(negative.cast("int")).alias("negative_reviews"),
        )
    )
    return _stamp(counts.withColumn("alert", F.col("negative_reviews") >= F.lit(min_count)), as_of)


def sales_vs_target(
    transactions: DataFrame, as_of: date, target_uplift_pct: Decimal | float
) -> DataFrame:
    """The day's revenue per franchise against a target of its average daily revenue on all
    earlier days, plus the uplift. A franchise with no sales that day has revenue 0."""
    first = _first_day(transactions, as_of)
    earlier_days = (as_of - first).days
    seen = _until(transactions, as_of)
    totals = seen.groupBy("franchiseID").agg(
        F.sum(F.when(_day() == F.lit(as_of), F.col("totalPrice")).otherwise(0)).alias("revenue"),
        F.sum(F.when(_day() < F.lit(as_of), F.col("totalPrice")).otherwise(0)).alias(
            "earlier_revenue"
        ),
    )
    baseline = (
        F.col("earlier_revenue") / F.lit(earlier_days)
        if earlier_days
        else F.lit(None).cast("double")
    )
    result = (
        totals.withColumn("earlier_avg_revenue", baseline)
        .withColumn("target", F.col("earlier_avg_revenue") * (1 + F.lit(float(target_uplift_pct))))
        .withColumn("attainment", F.when(F.col("target") > 0, F.col("revenue") / F.col("target")))
        .drop("earlier_revenue")
    )
    return _stamp(result, as_of)

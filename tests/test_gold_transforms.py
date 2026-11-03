from datetime import date, timedelta
from decimal import Decimal

import pytest

from bakehouse_core.transforms import gold

pytestmark = pytest.mark.spark

D1 = date(2024, 5, 1)


def txns(spark, rows):
    """rows: (day offset, franchise, customer, total)."""
    data = [(i, f, c, 1, t, D1 + timedelta(days=d)) for i, (d, f, c, t) in enumerate(rows, start=1)]
    schema = (
        "transactionID long, franchiseID long, customerID long, quantity long, "
        "totalPrice long, _virtual_date date"
    )
    return spark.createDataFrame(data, schema)


def by(df, key="franchiseID"):
    return {r[key]: r.asDict() for r in df.collect()}


def test_daily_kpis_only_cover_the_day(spark):
    t = txns(spark, [(0, 1, 10, 100), (0, 1, 11, 50), (0, 2, 10, 20), (1, 1, 10, 999)])
    out = by(gold.franchise_daily_kpis(t, D1, Decimal("0.10")))
    assert set(out) == {1, 2}
    assert out[1]["transactions"] == 2
    assert out[1]["revenue"] == 150
    assert out[1]["unique_customers"] == 2
    assert out[1]["avg_basket"] == 75
    assert out[1]["revenue_at_promo"] == pytest.approx(135)
    assert out[1]["as_of_date"] == D1


def test_flags_mark_a_franchise_whose_recent_revenue_drops(spark):
    # Franchise 1 earns 100 a day for 6 days then 40 for the last 3. Franchise 2 stays level.
    rows = []
    for d in range(9):
        rows.append((d, 1, 10, 100 if d < 6 else 40))
        rows.append((d, 2, 10, 100))
    as_of = D1 + timedelta(days=8)
    out = by(gold.franchise_flags(txns(spark, rows), as_of, Decimal("0.20"), 5))
    assert out[1]["underperforming"] is True
    assert out[1]["change_pct"] == pytest.approx(-0.6)
    assert out[2]["underperforming"] is False
    assert out[2]["revenue_rank"] == 1
    assert out[1]["revenue_rank"] == 2


def test_flags_need_enough_earlier_transactions(spark):
    rows = [(d, 1, 10, 100 if d < 6 else 10) for d in range(9)]
    out = by(gold.franchise_flags(txns(spark, rows), D1 + timedelta(days=8), Decimal("0.2"), 50))
    assert out[1]["eligible"] is False
    assert out[1]["underperforming"] is False
    assert out[1]["revenue_rank"] is None


def test_flags_have_no_comparison_in_the_first_days(spark):
    out = by(gold.franchise_flags(txns(spark, [(0, 1, 10, 100)]), D1, Decimal("0.2"), 1))
    assert out[1]["change_pct"] is None
    assert out[1]["underperforming"] is False


def test_loyalty_tiers_follow_thresholds_and_lookback(spark):
    rows = [(0, 1, 1, 300), (5, 1, 2, 120), (5, 1, 3, 10), (0, 1, 4, 900)]
    t = txns(spark, rows)
    as_of = D1 + timedelta(days=5)
    out = by(gold.customer_loyalty_tiers(t, as_of, 250, 100, 30), "customerID")
    assert {c: r["tier"] for c, r in out.items()} == {
        1: "gold",
        2: "silver",
        3: "bronze",
        4: "gold",
    }
    short = by(gold.customer_loyalty_tiers(t, as_of, 250, 100, 3), "customerID")
    assert set(short) == {2, 3}  # day 0 sales fall outside a 3-day window


def franchise_rows(spark, rows):
    """rows: (franchiseID, supplierID, valid_from offset, valid_to offset or None)."""
    data = [
        (f, s, D1 + timedelta(days=a), None if b is None else D1 + timedelta(days=b))
        for f, s, a, b in rows
    ]
    return spark.createDataFrame(
        data, "franchiseID long, supplierID long, _valid_from date, _valid_to date"
    )


def test_supplier_concentration_flags_a_dominant_supplier(spark):
    t = txns(spark, [(0, 1, 10, 700), (0, 2, 10, 200), (0, 3, 10, 100)])
    f = franchise_rows(spark, [(1, 100, 0, None), (2, 200, 0, None), (3, 200, 0, None)])
    out = by(gold.supplier_concentration(t, f, D1, Decimal("0.60")), "supplierID")
    assert out[100]["revenue_share"] == pytest.approx(0.7)
    assert out[100]["concentrated"] is True
    assert out[200]["concentrated"] is False
    assert out[200]["franchises"] == 2


def test_supplier_concentration_uses_the_supplier_in_force_that_day(spark):
    t = txns(spark, [(0, 1, 10, 100), (1, 1, 10, 100)])
    f = franchise_rows(spark, [(1, 100, 0, 1), (1, 200, 1, None)])
    day0 = by(gold.supplier_concentration(t, f, D1, Decimal("0.6")), "supplierID")
    day1 = by(
        gold.supplier_concentration(t, f, D1 + timedelta(days=1), Decimal("0.6")), "supplierID"
    )
    assert set(day0) == {100}
    assert set(day1) == {200}


def test_review_alerts_count_negative_reviews_in_the_window(spark):
    rows = [
        ("Terrible service", 1, D1),
        ("Rude staff, never again", 1, D1),
        ("Lovely croissants", 1, D1),
        ("Stale bread", 1, D1 - timedelta(days=30)),  # outside the window
        ("Wrong order", 2, D1),
    ]
    reviews = spark.createDataFrame(rows, "review string, franchiseID long, _virtual_date date")
    out = by(gold.review_alerts(reviews, D1, 2))
    assert out[1]["negative_reviews"] == 2
    assert out[1]["reviews"] == 3
    assert out[1]["alert"] is True
    assert out[2]["alert"] is False


def test_sales_vs_target_uses_earlier_average(spark):
    rows = [(0, 1, 10, 100), (1, 1, 10, 100), (2, 1, 10, 126), (2, 2, 10, 5)]
    out = by(gold.sales_vs_target(txns(spark, rows), D1 + timedelta(days=2), Decimal("0.05")))
    assert out[1]["revenue"] == 126
    assert out[1]["target"] == pytest.approx(105)
    assert out[1]["attainment"] == pytest.approx(1.2)
    assert out[2]["target"] == pytest.approx(0)
    assert out[2]["attainment"] is None  # no earlier sales, so no target


def test_sales_vs_target_gives_zero_revenue_to_a_quiet_franchise(spark):
    rows = [(0, 1, 10, 100), (1, 2, 10, 50)]
    out = by(gold.sales_vs_target(txns(spark, rows), D1 + timedelta(days=1), Decimal("0")))
    assert out[1]["revenue"] == 0
    assert out[1]["attainment"] == pytest.approx(0)


def test_sales_vs_target_has_no_target_on_the_first_day(spark):
    out = by(gold.sales_vs_target(txns(spark, [(0, 1, 10, 100)]), D1, Decimal("0.05")))
    assert out[1]["target"] is None
    assert out[1]["attainment"] is None

# Databricks notebook source
# MAGIC %md
# MAGIC # 03 Promo what-if
# MAGIC
# MAGIC What would revenue have been if every price had a discount? Daily network revenue from the gold
# MAGIC `franchise_daily_kpis` table is compared with a projection at a discount rate, with an assumed
# MAGIC volume uplift on top (a cheaper price sells more units).
# MAGIC
# MAGIC - `promo_discount_rate`: a fraction between 0 and 0.50, the same range as the app parameter.
# MAGIC - `volume_uplift_pct`: how many percent more units the discount sells. 0 means no response.
# MAGIC
# MAGIC The gold column `revenue_at_promo` uses the discount saved in the app and no volume change.
# MAGIC This notebook lets you try other values. Run it on serverless compute. It only reads tables.
# MAGIC
# MAGIC The calculation comes from the `bakehouse_core` wheel, which `bundle deploy` uploads to the
# MAGIC `bakehouse_dev.ops.artifacts` volume. There is no fallback: if the install fails, so does the
# MAGIC notebook. `%pip` cannot take a variable, so the catalog in the path is written out.

# COMMAND ----------

# MAGIC %pip install /Volumes/bakehouse_dev/ops/artifacts/.internal/bakehouse_core-0.1.0-py3-none-any.whl

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "bakehouse_dev", "Catalog")
dbutils.widgets.text("promo_discount_rate", "0.10", "Discount rate (0 to 0.50)")
dbutils.widgets.text("volume_uplift_pct", "5", "Volume uplift (%)")

catalog = dbutils.widgets.get("catalog")
discount_rate = float(dbutils.widgets.get("promo_discount_rate"))
volume_uplift = float(dbutils.widgets.get("volume_uplift_pct")) / 100

# COMMAND ----------

import matplotlib.pyplot as plt
import pandas as pd
from pyspark.sql import functions as F

import bakehouse_core
from bakehouse_core.transforms.promo import break_even_uplift, project_revenue

print("bakehouse_core loaded from:", bakehouse_core.__file__)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Revenue history
# MAGIC
# MAGIC Each gold snapshot holds one virtual day, so the network's revenue for a day is the sum over
# MAGIC its franchises.

# COMMAND ----------

daily = (
    spark.table(f"{catalog}.gold.franchise_daily_kpis")
    .groupBy("as_of_date")
    .agg(F.sum("revenue").alias("revenue"))
    .orderBy("as_of_date")
    .toPandas()
)
if daily.empty:
    raise ValueError("No gold data yet. Run the simulator and the bakehouse_etl job first.")

daily["as_of_date"] = pd.to_datetime(daily["as_of_date"])
daily["revenue"] = daily["revenue"].astype(float)
daily["projected"] = project_revenue(daily["revenue"], discount_rate, volume_uplift)

actual_total = daily["revenue"].sum()
projected_total = daily["projected"].sum()
print(f"Days: {len(daily)}")
print(f"Revenue so far:      {actual_total:>12,.0f}")
print(f"At {discount_rate:.0%} off, {volume_uplift:+.0%} volume: {projected_total:>8,.0f}")
print(f"Change:              {projected_total / actual_total - 1:>+12.1%}")
print(f"Uplift needed to break even at this discount: {break_even_uplift(discount_rate):.1%}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 1: actual and projected daily revenue

# COMMAND ----------

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE = "#2a78d6", "#eb6834"


def tidy(ax, title: str, ylabel: str) -> None:
    """Thin marks, quiet grid, no frame, text in ink rather than series colours."""
    ax.set_title(title, loc="left", color=INK, fontsize=12)
    ax.set_ylabel(ylabel, color=MUTED)
    ax.tick_params(colors=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(frameon=False, labelcolor=INK)


fig, ax = plt.subplots(figsize=(9, 4))
ax.plot(daily["as_of_date"], daily["revenue"], color=BLUE, linewidth=2, label="Actual")
ax.plot(
    daily["as_of_date"],
    daily["projected"],
    color=ORANGE,
    linewidth=2,
    linestyle="--",
    label=f"Projected ({discount_rate:.0%} off, {volume_uplift:+.0%} volume)",
)
ax.set_ylim(bottom=0)
fig.autofmt_xdate()
tidy(ax, "Daily network revenue", "Revenue")
plt.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 2: how much volume pays for the discount?
# MAGIC
# MAGIC Projected revenue as a share of actual revenue for a range of volume uplifts, at the discount
# MAGIC chosen above. Where the line crosses 100% the extra units exactly cover the lower price.

# COMMAND ----------

uplifts = [i / 100 for i in range(0, 41)]
share = [project_revenue([1.0], discount_rate, u)[0] * 100 for u in uplifts]
break_even = break_even_uplift(discount_rate)

fig, ax = plt.subplots(figsize=(9, 4))
ax.plot([u * 100 for u in uplifts], share, color=BLUE, linewidth=2, label="Projected revenue")
ax.axhline(100, color=MUTED, linewidth=1, linestyle=":", label="Actual revenue (100%)")
if break_even <= uplifts[-1]:
    ax.axvline(break_even * 100, color=ORANGE, linewidth=2, label=f"Break even: {break_even:.1%}")
ax.set_xlabel("Volume uplift (%)", color=MUTED)
tidy(ax, f"Projected revenue at {discount_rate:.0%} off, by volume uplift", "% of actual revenue")
plt.show()
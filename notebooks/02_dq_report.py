# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Data-quality report
# MAGIC
# MAGIC What the silver build did with the rows it was given: how many it kept, how many it put in
# MAGIC quarantine and why, how null rates moved from run to run, and which files arrived late.
# MAGIC
# MAGIC Run the simulator with `scenarios=duplicates;null_spike;late_files` and then the ETL to give
# MAGIC it something to show. Run it on serverless compute. It only reads tables.
# MAGIC
# MAGIC The log parsing comes from the `bakehouse_core` wheel, which `bundle deploy` uploads to the
# MAGIC `bakehouse_dev.ops.artifacts` volume. There is no fallback: if the install fails, so does the
# MAGIC notebook. `%pip` cannot take a variable, so the catalog in the path is written out.

# COMMAND ----------

# MAGIC %pip install /Volumes/bakehouse_dev/ops/artifacts/.internal/bakehouse_core-0.1.0-py3-none-any.whl

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "bakehouse_dev", "Catalog")
dbutils.widgets.text("runs", "10", "Recent silver runs to show")

catalog = dbutils.widgets.get("catalog")
runs = int(dbutils.widgets.get("runs"))

# COMMAND ----------

from pyspark.sql import functions as F

import bakehouse_core
from bakehouse_core.dq.report import parse_silver_details

print("bakehouse_core loaded from:", bakehouse_core.__file__)


def table(schema: str, name: str):
    return spark.table(f"{catalog}.{schema}.{name}")


# COMMAND ----------

# MAGIC %md
# MAGIC ## Recent silver builds

# COMMAND ----------

recent = (
    table("ops", "run_log")
    .filter(F.col("job") == "silver_build")
    .orderBy(F.col("started_at").desc())
    .limit(runs)
)
display(
    recent.select(
        "run_id",
        "status",
        "virtual_date",
        "triggered_by",
        "started_at",
        (F.col("finished_at").cast("long") - F.col("started_at").cast("long")).alias("seconds"),
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Quarantine
# MAGIC
# MAGIC Rows that failed a check are kept in `silver.transactions_quarantine` and
# MAGIC `silver.reviews_quarantine` with the reason, so nothing is silently dropped.

# COMMAND ----------

quarantine = None
for entity in ("transactions", "reviews"):
    part = table("silver", f"{entity}_quarantine").select(
        F.lit(entity).alias("entity"), "_virtual_date", "_dq_reason"
    )
    quarantine = part if quarantine is None else quarantine.unionByName(part)

display(quarantine.groupBy("entity", "_dq_reason").count().orderBy(F.col("count").desc()))

# COMMAND ----------

display(quarantine.groupBy("_virtual_date", "entity").count().orderBy("_virtual_date", "entity"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## What each batch did, and the null rates
# MAGIC
# MAGIC Each `silver_build` run saves its counts as JSON in `ops.run_log.details`. `already_seen` is
# MAGIC what a batch held that silver did not take: duplicates, or rows from an earlier file. The null
# MAGIC rate is the share of rows missing `paymentMethod` in a batch. A rate above the `dq_max_null_pct`
# MAGIC parameter quarantines those rows.

# COMMAND ----------

batch_rows, null_rows = [], []
for run in recent.collect():
    batches, nulls = parse_silver_details(run["details"])
    meta = {"run_id": run["run_id"], "virtual_date": run["virtual_date"]}
    batch_rows += [meta | row for row in batches]
    null_rows += [meta | row for row in nulls]

batch_schema = (
    "run_id string, virtual_date date, entity string, batch int, rows_in long, applied long, "
    "quarantined long, already_seen long, reasons string"
)
null_schema = (
    "run_id string, virtual_date date, entity string, batch int, column string, null_rate double"
)

display(
    spark.createDataFrame(batch_rows, batch_schema).orderBy(
        F.col("virtual_date").desc(), "entity", "batch"
    )
)

# COMMAND ----------

display(
    spark.createDataFrame(null_rows, null_schema)
    .withColumn("null_pct", F.round(F.col("null_rate") * 100, 1))
    .orderBy(F.col("virtual_date").desc(), "batch")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Late files
# MAGIC
# MAGIC A transaction is late when its event time is earlier than the day of the file it arrived in.
# MAGIC Late rows are kept in silver on their real day, so they show up here but do not change a day's
# MAGIC gold snapshot until that day is rebuilt.

# COMMAND ----------

transactions = table("silver", "transactions").withColumn(
    "days_late", F.datediff(F.col("_virtual_date"), F.to_date(F.col("dateTime")))
)

by_file_day = (
    transactions.groupBy("_virtual_date")
    .agg(
        F.count(F.lit(1)).alias("rows"),
        F.sum((F.col("days_late") > 0).cast("int")).alias("late_rows"),
    )
    .withColumn("late_pct", F.round(100 * F.col("late_rows") / F.col("rows"), 1))
    .orderBy("_virtual_date")
)
display(by_file_day)

# COMMAND ----------

display(
    transactions.filter(F.col("days_late") > 0)
    .groupBy("_virtual_date", "days_late")
    .count()
    .orderBy("_virtual_date", "days_late")
)

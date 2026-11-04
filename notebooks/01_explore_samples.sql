-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 01 Explore `samples.bakehouse`
-- MAGIC
-- MAGIC A plain SQL tour of the public sample data the whole PoC is built on: what is in each table,
-- MAGIC how far the sales dates reach, and how much of that the simulator has released so far.
-- MAGIC
-- MAGIC Run it on serverless compute. The `catalog` widget only matters for the last section.

-- COMMAND ----------

-- DBTITLE 1,Show catalog parameter
SELECT :catalog AS catalog;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## The tables and their sizes

-- COMMAND ----------

SELECT 'sales_transactions' AS table_name, COUNT(*) AS row_count FROM samples.bakehouse.sales_transactions
UNION ALL SELECT 'sales_customers', COUNT(*) FROM samples.bakehouse.sales_customers
UNION ALL SELECT 'sales_franchises', COUNT(*) FROM samples.bakehouse.sales_franchises
UNION ALL SELECT 'sales_suppliers', COUNT(*) FROM samples.bakehouse.sales_suppliers
UNION ALL SELECT 'media_customer_reviews', COUNT(*) FROM samples.bakehouse.media_customer_reviews
ORDER BY row_count DESC;

-- COMMAND ----------

DESCRIBE TABLE samples.bakehouse.sales_transactions;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Date range
-- MAGIC
-- MAGIC The simulator replays these days one at a time, so the range sets how far a virtual clock can run.

-- COMMAND ----------

SELECT
  MIN(dateTime) AS first_sale,
  MAX(dateTime) AS last_sale,
  COUNT(DISTINCT DATE(dateTime)) AS distinct_days
FROM samples.bakehouse.sales_transactions;

-- COMMAND ----------

SELECT
  DATE(dateTime) AS sales_date,
  COUNT(*) AS transactions,
  COUNT(DISTINCT franchiseID) AS active_franchises,
  ROUND(SUM(totalPrice), 2) AS revenue
FROM samples.bakehouse.sales_transactions
GROUP BY DATE(dateTime)
ORDER BY sales_date;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## What sells

-- COMMAND ----------

SELECT
  product,
  COUNT(*) AS transactions,
  SUM(quantity) AS units,
  ROUND(SUM(totalPrice), 2) AS revenue,
  ROUND(AVG(unitPrice), 2) AS avg_unit_price
FROM samples.bakehouse.sales_transactions
GROUP BY product
ORDER BY revenue DESC;

-- COMMAND ----------

SELECT
  paymentMethod,
  COUNT(*) AS transactions,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_transactions
FROM samples.bakehouse.sales_transactions
GROUP BY paymentMethod
ORDER BY transactions DESC;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Franchises and their suppliers
-- MAGIC
-- MAGIC Each franchise points at one supplier, and some franchises point at a supplier that has no row
-- MAGIC in the suppliers table. The gold `supplier_concentration` table flags those with `supplier_known = false`.

-- COMMAND ----------

SELECT
  f.country,
  f.size,
  COUNT(*) AS franchises,
  COUNT(s.supplierID) AS with_known_supplier
FROM samples.bakehouse.sales_franchises AS f
LEFT JOIN samples.bakehouse.sales_suppliers AS s ON s.supplierID = f.supplierID
GROUP BY f.country, f.size
ORDER BY franchises DESC;

-- COMMAND ----------

SELECT
  franchiseID,
  name,
  city,
  country,
  size
FROM samples.bakehouse.sales_franchises
ORDER BY franchiseID
LIMIT 10;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Reviews

-- COMMAND ----------

SELECT
  franchiseID,
  COUNT(*) AS reviews,
  MIN(review_date) AS first_review,
  MAX(review_date) AS last_review
FROM samples.bakehouse.media_customer_reviews
GROUP BY franchiseID
ORDER BY reviews DESC
LIMIT 10;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## How much has the simulator released?
-- MAGIC
-- MAGIC The virtual clock lives in `<catalog>.ops.sim_clock`. Compare it with the range above.

-- COMMAND ----------

SELECT virtual_date, phase, updated_at
FROM IDENTIFIER(:catalog || '.ops.sim_clock');
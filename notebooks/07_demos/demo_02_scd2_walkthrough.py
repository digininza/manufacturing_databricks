# Databricks notebook source
# MAGIC %md
# MAGIC # Demo 2 · SCD Type 2 and point-in-time facts
# MAGIC Day 2 moved `M-P01-03` from line P01-L2 to P01-L3 and put `M-P02-04` into MAINTENANCE;
# MAGIC ERP raised the unit cost of `PRD-1001` and changed the cycle time of `PRD-1006`.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

display(
    spark.sql(
        f"""
  SELECT machine_sk, machine_id, line_id, status, effective_from, effective_to, is_current
  FROM {config.fq('gold','dim_machine')} WHERE machine_id IN ('M-P01-03','M-P02-04') ORDER BY machine_id, effective_from"""
    )
)  # noqa: F821

display(
    spark.sql(
        f"""
  SELECT product_sk, product_id, unit_cost, std_cycle_time_sec, effective_from, effective_to, is_current
  FROM {config.fq('gold','dim_product')} WHERE product_id IN ('PRD-1001','PRD-1006') ORDER BY product_id, effective_from"""
    )
)  # noqa: F821

# COMMAND ----------

# MAGIC %md ### Facts keep pointing at the version valid on their day (history is NOT rewritten)
display(
    spark.sql(
        f"""
  SELECT f.production_date, f.machine_id, d.line_id AS line_on_that_day, d.is_current, f.units_produced
  FROM {config.fq('gold','fact_production_daily')} f JOIN {config.fq('gold','dim_machine')} d ON d.machine_sk = f.machine_sk
  WHERE f.machine_id = 'M-P01-03' ORDER BY f.production_date"""
    )
)  # noqa: F821

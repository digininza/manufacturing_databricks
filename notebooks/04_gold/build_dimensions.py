# Databricks notebook source
# MAGIC %md
# MAGIC # Gold · dimensions (SCD2 machine/product, SCD1 plant/supplier, static date)

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.gold.dimensions import build_all_dimensions

ctx = RunContext(config, "mfg_batch_pipeline", new_run_id("gold_dims"), JOB_RUN_ID)
build_all_dimensions(spark, ctx)
AuditLogger(spark, ctx).log("ALL", "GOLD_DIMENSIONS_BUILT")
display(
    spark.sql(
        f"SELECT machine_id, line_id, status, effective_from, effective_to, is_current FROM {config.fq('gold','dim_machine')} ORDER BY machine_id, effective_from"
    )
)  # noqa: F821

# Databricks notebook source
# MAGIC %md
# MAGIC # Gold · facts (incremental via Change Data Feed, affected-dates recompute, SILVER→GOLD reconciliation)

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.gold.facts import build_all_facts

ctx = RunContext(config, "mfg_batch_pipeline", new_run_id("gold_facts"), JOB_RUN_ID)
results = build_all_facts(spark, ctx)  # raises (watermark untouched) if reconciliation fails
AuditLogger(spark, ctx).log("ALL", "GOLD_FACTS_BUILT", {"recon": [r.__dict__ for r in results]})
display(
    spark.sql(
        f"SELECT production_date, machine_id, availability, performance, quality, oee FROM {config.fq('gold','fact_machine_daily')} ORDER BY production_date DESC, machine_id"
    )
)  # noqa: F821

# Databricks notebook source
# MAGIC %md
# MAGIC # Publish gold → Snowflake (CDF changes → STAGE → MERGE → reconcile → watermark)

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.publish.snowflake_publisher import publish_all

ctx = RunContext(config, "mfg_batch_pipeline", new_run_id("sf_publish"), JOB_RUN_ID)
results = publish_all(spark, ctx)
AuditLogger(spark, ctx).log("ALL", "SNOWFLAKE_PUBLISHED", {"tables": [r.entity_name for r in results]})
for r in results:
    print(f"{r.entity_name:<26} gold={r.source_count:>8} snowflake={r.target_count:>8} {r.status} {r.detail}")

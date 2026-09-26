# Databricks notebook source
# MAGIC %md
# MAGIC # Table maintenance · OPTIMIZE (liquid clustering / Z-ORDER) · VACUUM · ANALYZE
# MAGIC Strategy per table and the reasoning: `src/framework/table_maintenance.py`.
# MAGIC Runs weekly (Sunday 03:00 UTC) outside the batch window: OPTIMIZE rewrites files and competes
# MAGIC for I/O with MERGEs; it is also conflict-free with them thanks to row-level concurrency, but cheaper off-peak.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.framework.table_maintenance import run_maintenance

ctx = RunContext(config, "mfg_table_maintenance", new_run_id("maint"), JOB_RUN_ID)
results = run_maintenance(spark, ctx)
AuditLogger(spark, ctx).log("ALL", "TABLE_MAINTENANCE", {"results": results})
display(spark.createDataFrame(results))

failed = [r for r in results if r["status"] != "OK"]
if failed:
    raise Exception(f"{len(failed)} maintenance statement(s) failed: {failed}")

# COMMAND ----------

# MAGIC %md ### Check the effect: file count and size before/after (run DESCRIBE DETAIL before and after)
display(spark.sql(f"DESCRIBE DETAIL {config.fq('silver', 'mes_production_log')}").select("numFiles", "sizeInBytes", "clusteringColumns"))

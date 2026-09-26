# Databricks notebook source
# MAGIC %md
# MAGIC # Refresh the Power BI Import semantic model (only reached if publish + reconciliation succeeded)

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.publish.powerbi_refresh import trigger_refresh

ctx = RunContext(config, "mfg_batch_pipeline", new_run_id("pbi_refresh"), JOB_RUN_ID)
status = trigger_refresh(ctx, get_widget("powerbi_workspace_id", "REPLACE"), get_widget("powerbi_dataset_id", "REPLACE"))
AuditLogger(spark, ctx).log("NorthForge_Manufacturing", "POWERBI_REFRESH_TRIGGERED", {"http_status": status})
print("refresh accepted:", status)

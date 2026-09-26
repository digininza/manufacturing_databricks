# Databricks notebook source
# MAGIC %md
# MAGIC # Demo 1 · What CDC looks like, and how silver applies it
# MAGIC Day-2 production_log batch contains: new runs (op 2), two successive corrections of run
# MAGIC 100001 (op 4 twice), a correction of the quarantined run 100009 (op 4), and two deletes (op 1).

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

bronze = config.fq("bronze", "mes_production_log")
silver = config.fq("silver", "mes_production_log")

# COMMAND ----------

# MAGIC %md ### 1. Raw CDC rows as ADF landed them (bronze keeps them verbatim)
display(
    spark.sql(
        f"""
  SELECT cdc_operation,
         CASE cdc_operation WHEN '1' THEN 'DELETE' WHEN '2' THEN 'INSERT' WHEN '3' THEN 'UPDATE-BEFORE' WHEN '4' THEN 'UPDATE-AFTER' END AS meaning,
         cdc_start_lsn, cdc_seqval, cdc_commit_ts, production_log_id, units_produced, units_scrapped, _ingestion_run_id
  FROM {bronze}
  WHERE production_log_id IN ('100001','100002','100003','100009')
  ORDER BY production_log_id, cdc_start_lsn, cdc_seqval"""
    )
)  # noqa: F821

# COMMAND ----------

# MAGIC %md ### 2. Silver after applying: one row per key, latest version, soft-deleted keys flagged
display(
    spark.sql(
        f"""
  SELECT production_log_id, units_produced, units_scrapped, _is_deleted, _sequence, cdc_operation AS last_op, _pipeline_run_id
  FROM {silver} WHERE production_log_id IN (100001, 100002, 100003, 100009) ORDER BY production_log_id"""
    )
)  # noqa: F821

# COMMAND ----------

# MAGIC %md ### 3. Day-1 quarantine: the bad rows, with the rule(s) they failed
display(
    spark.sql(f"SELECT production_log_id, units_produced, units_scrapped, _dq_errors, _quarantined_ts FROM {config.fq('quarantine','mes_production_log')}")
)  # noqa: F821

# COMMAND ----------

# MAGIC %md ### 4. Delta history of silver: every MERGE with its row metrics (time travel = audit)
display(spark.sql(f"DESCRIBE HISTORY {silver}").select("version", "timestamp", "operation", "operationMetrics"))  # noqa: F821

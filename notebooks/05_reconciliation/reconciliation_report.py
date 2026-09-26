# Databricks notebook source
# MAGIC %md
# MAGIC # Reconciliation report (end-to-end, per run date)
# MAGIC One screen answering "can the business trust today's numbers?":
# MAGIC ADF (control DB) → Bronze → Silver → Gold → Snowflake. Fails the task if anything
# MAGIC unresolved is FAILED, which blocks the Power BI refresh task.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

recon_tbl = config.fq("control", "reconciliation_results")
summary = spark.sql(
    f"""
    WITH latest AS (
      SELECT *, row_number() OVER (PARTITION BY recon_type, entity_name, coalesce(regexp_extract(detail, 'run_id=([^ ]+)', 1), '')
                                   ORDER BY checked_ts DESC) AS rn
      FROM {recon_tbl} WHERE checked_ts >= current_date() - INTERVAL 1 DAY)
    SELECT recon_type, entity_name, source_count, target_count, difference, source_amount, target_amount, status, detail, checked_ts
    FROM latest WHERE rn = 1
    ORDER BY CASE recon_type WHEN 'ADF_TO_BRONZE' THEN 1 WHEN 'BRONZE_TO_SILVER' THEN 2 WHEN 'SILVER_TARGET_VERIFY' THEN 3
                             WHEN 'SILVER_TO_GOLD' THEN 4 ELSE 5 END, entity_name
    """
)
display(summary)  # noqa: F821
display(
    spark.sql(
        f"SELECT entity_name, rule_name, severity, sum(failed_count) AS failed FROM {config.fq('control','dq_results')} WHERE checked_ts >= current_date() - INTERVAL 1 DAY GROUP BY ALL HAVING failed > 0 ORDER BY failed DESC"
    )
)  # noqa: F821

open_failures = summary.filter("status <> 'PASSED'").count()
if open_failures:
    raise Exception(f"{open_failures} unresolved reconciliation failure(s): Power BI refresh is blocked")

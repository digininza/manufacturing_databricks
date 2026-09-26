# Databricks notebook source
# MAGIC %md
# MAGIC # Demo 3 · Reconciliation failure blocks the pipeline
# MAGIC 1. Run `00_setup/01_simulate_adf_landing` with `day=2`, `break_manifest_for=erp_product`
# MAGIC    (the manifest claims 5 more rows than were written, like a partially copied file).
# MAGIC 2. Run `01_bronze/run_autoloader_bronze`: it **fails** after ingesting, raising an alert.
# MAGIC 3. The job's silver/gold/publish tasks never start → Snowflake and Power BI keep yesterday's
# MAGIC    consistent data instead of today's wrong data.
# MAGIC 4. Fix: re-run the ADF entity (or here: re-run the landing without the flag). Re-run bronze: PASSED.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

display(
    spark.sql(
        f"""
  SELECT recon_type, entity_name, source_count AS adf_rows_copied, target_count AS bronze_rows, difference, status, detail, checked_ts
  FROM {config.fq('control','reconciliation_results')} WHERE recon_type = 'ADF_TO_BRONZE' ORDER BY checked_ts DESC LIMIT 20"""
    )
)  # noqa: F821
display(spark.sql(f"SELECT * FROM {config.fq('control','vw_failed_reconciliations')} ORDER BY checked_ts DESC"))  # noqa: F821

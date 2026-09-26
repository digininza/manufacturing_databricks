-- =============================================================================
-- Databricks-side control tables (the ADF-side ones live in Azure SQL: sql/control_db/)
-- =============================================================================

-- Every layer/entity/micro-batch execution: counts in, out, quarantined, duplicates.
CREATE TABLE IF NOT EXISTS ${catalog}.control.pipeline_run_log (
  run_id STRING, job_run_id STRING, pipeline_name STRING, layer STRING, entity_name STRING,
  batch_id BIGINT, status STRING COMMENT 'SUCCESS | FAILED | RECON_FAILED',
  rows_in BIGINT, rows_valid BIGINT, rows_quarantined BIGINT, rows_duplicate BIGINT, rows_written BIGINT,
  error_message STRING, start_ts TIMESTAMP, end_ts TIMESTAMP
) CLUSTER BY (entity_name, start_ts);

-- Significant events (job start/end, schema change, manual reset, publish) as JSON detail.
CREATE TABLE IF NOT EXISTS ${catalog}.control.audit_log (
  run_id STRING, job_run_id STRING, pipeline_name STRING, entity_name STRING,
  event_type STRING, event_detail STRING, event_ts TIMESTAMP
) CLUSTER BY (event_ts);

-- Per rule, per batch failure counts -> DQ trend dashboards.
CREATE TABLE IF NOT EXISTS ${catalog}.control.dq_results (
  run_id STRING, entity_name STRING, batch_id BIGINT, rule_name STRING, severity STRING,
  failed_count BIGINT, total_count BIGINT, checked_ts TIMESTAMP
) CLUSTER BY (entity_name, checked_ts);

-- ADF_TO_BRONZE | BRONZE_TO_SILVER | SILVER_TARGET_VERIFY | SILVER_TO_GOLD | GOLD_TO_SNOWFLAKE
CREATE TABLE IF NOT EXISTS ${catalog}.control.reconciliation_results (
  recon_id STRING, run_id STRING, recon_type STRING, entity_name STRING,
  source_count BIGINT, target_count BIGINT, difference BIGINT,
  source_amount DOUBLE, target_amount DOUBLE, status STRING, detail STRING, checked_ts TIMESTAMP
) CLUSTER BY (recon_type, checked_ts);

-- Delta-version watermarks for CDF consumers (gold_facts, snowflake_publish).
-- Advanced ONLY after the consumer's reconciliation passed.
CREATE TABLE IF NOT EXISTS ${catalog}.control.watermarks (
  consumer STRING, source_table STRING, last_version BIGINT, run_id STRING, updated_ts TIMESTAMP
);

-- Operational view: latest status per entity & layer (used by the on-call dashboard).
CREATE OR REPLACE VIEW ${catalog}.control.vw_latest_entity_status AS
SELECT * FROM (
  SELECT *, row_number() OVER (PARTITION BY layer, entity_name ORDER BY end_ts DESC) AS rn
  FROM ${catalog}.control.pipeline_run_log
) WHERE rn = 1;

CREATE OR REPLACE VIEW ${catalog}.control.vw_failed_reconciliations AS
SELECT * FROM ${catalog}.control.reconciliation_results WHERE status <> 'PASSED';

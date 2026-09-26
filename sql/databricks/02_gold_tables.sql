-- =============================================================================
-- GOLD STAR SCHEMA. All gold tables have Change Data Feed ON: the Snowflake
-- publisher reads only what changed since its last successful publish.
-- Surrogate keys are deterministic xxhash64 BIGINTs (see src/gold/scd2.py). -1 = Unknown.
-- =============================================================================

-- ------------------------------------------------------------------ dimensions
CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_date (
  date_key INT NOT NULL, calendar_date DATE, year INT, quarter INT, month INT, month_name STRING,
  iso_week INT, day_of_week INT, day_name STRING, is_weekend BOOLEAN, fiscal_year STRING
) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_plant (
  plant_sk BIGINT NOT NULL, plant_id STRING, plant_name STRING, city STRING, country_code STRING, timezone STRING,
  _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_supplier (
  supplier_sk BIGINT NOT NULL, supplier_id STRING, supplier_name STRING, _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_machine (   -- SCD TYPE 2
  machine_sk BIGINT NOT NULL COMMENT 'xxhash64(machine_id, effective_from)',
  machine_id STRING COMMENT 'business key from MES',
  machine_name STRING, plant_id STRING, line_id STRING, machine_type STRING, manufacturer STRING,
  install_date DATE, status STRING, rated_units_per_hour INT, is_source_deleted BOOLEAN,
  attr_hash STRING COMMENT 'sha256 of tracked attributes: change detection',
  effective_from TIMESTAMP, effective_to TIMESTAMP COMMENT '9999-12-31 for the current version',
  is_current BOOLEAN, _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) CLUSTER BY (machine_id) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_product (   -- SCD TYPE 2
  product_sk BIGINT NOT NULL, product_id STRING, sku STRING, product_name STRING, product_family STRING,
  unit_cost DECIMAL(12,2), std_cycle_time_sec INT, attr_hash STRING,
  effective_from TIMESTAMP, effective_to TIMESTAMP, is_current BOOLEAN, _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) CLUSTER BY (product_id) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_shift (
  shift_code STRING NOT NULL, shift_name STRING, start_hour INT, end_hour INT, crosses_midnight BOOLEAN
) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.dim_defect_type (
  defect_code STRING NOT NULL, defect_description STRING, defect_category STRING, severity STRING
) TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

-- ------------------------------------------------------------------ facts
CREATE TABLE IF NOT EXISTS ${catalog}.gold.fact_production_daily (
  date_key INT, production_date DATE, plant_sk BIGINT, machine_sk BIGINT, product_sk BIGINT,
  machine_id STRING COMMENT 'degenerate: part of the grain / merge key',
  product_id STRING,
  production_runs BIGINT, units_produced BIGINT, units_scrapped BIGINT, units_good BIGINT,
  run_minutes DECIMAL(12,2), ideal_units INT,
  inspections BIGINT, sampled_units BIGINT, defect_units BIGINT, failed_inspections BIGINT,
  _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) CLUSTER BY (production_date, machine_id)
  COMMENT 'GRAIN: one row per production_date x machine x product'
  TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.fact_machine_daily (
  date_key INT, production_date DATE, machine_id STRING,
  run_minutes DECIMAL(12,2), ideal_units BIGINT, units_produced BIGINT, units_good BIGINT,
  downtime_minutes BIGINT, breakdowns BIGINT, machine_sk BIGINT, plant_sk BIGINT,
  anomaly_minutes BIGINT, avg_temperature_c DECIMAL(8,2), max_vibration_mm_s DECIMAL(8,3),
  planned_minutes INT, available_minutes BIGINT,
  availability DOUBLE, performance DOUBLE, quality DOUBLE, oee DOUBLE,
  _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) CLUSTER BY (production_date, machine_id)
  COMMENT 'GRAIN: one row per production_date x machine. OEE = availability x performance x quality'
  TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.fact_quality_inspection (
  inspection_id BIGINT, date_key INT, machine_sk BIGINT, product_sk BIGINT, defect_code STRING,
  production_log_id BIGINT, inspection_ts TIMESTAMP, sample_size INT, defect_count INT,
  inspection_result STRING, inspector_id STRING, _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) CLUSTER BY (date_key) COMMENT 'GRAIN: one row per inspection' TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.fact_maintenance_event (
  work_order_id STRING, reported_date_key INT, completed_date_key INT, machine_sk BIGINT, machine_id STRING,
  work_type STRING, priority STRING, status STRING, reported_at TIMESTAMP, started_at TIMESTAMP, completed_at TIMESTAMP,
  downtime_minutes INT, response_minutes INT, repair_minutes INT, technician STRING,
  _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) COMMENT 'GRAIN: one row per work order (accumulating snapshot)' TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS ${catalog}.gold.fact_supplier_delivery (
  delivery_id STRING, date_key INT, supplier_sk BIGINT, plant_sk BIGINT, material_code STRING, lot_number STRING,
  delivered_qty INT, rejected_qty INT, accepted_qty INT, _pipeline_run_id STRING, _gold_updated_ts TIMESTAMP
) COMMENT 'GRAIN: one row per delivery line' TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

-- ------------------------------------------------------------------ seeds + unknown members
MERGE INTO ${catalog}.gold.dim_shift t USING (
  SELECT * FROM VALUES ('A','Morning',6,14,false), ('B','Afternoon',14,22,false), ('C','Night',22,6,true), ('UNKNOWN','Unknown',NULL,NULL,false)
  AS v(shift_code, shift_name, start_hour, end_hour, crosses_midnight)) s ON t.shift_code = s.shift_code
WHEN NOT MATCHED THEN INSERT *;

MERGE INTO ${catalog}.gold.dim_defect_type t USING (
  SELECT * FROM VALUES
    ('NONE','No defect','NONE','NONE'), ('SCRATCH','Surface scratch','COSMETIC','MINOR'),
    ('DIMENSION_OOT','Dimension out of tolerance','DIMENSIONAL','MAJOR'), ('POROSITY','Casting porosity','MATERIAL','MAJOR'),
    ('BURR','Burr on machined edge','COSMETIC','MINOR'), ('CRACK','Crack','STRUCTURAL','CRITICAL'),
    ('WELD_SPATTER','Weld spatter','COSMETIC','MINOR')
  AS v(defect_code, defect_description, defect_category, severity)) s ON t.defect_code = s.defect_code
WHEN NOT MATCHED THEN INSERT *;

INSERT INTO ${catalog}.gold.dim_machine (machine_sk, machine_id, machine_name, plant_id, line_id, machine_type, manufacturer, status,
  effective_from, effective_to, is_current, attr_hash)
SELECT -1, 'UNKNOWN', 'Unknown machine', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'Unknown', 'UNKNOWN',
  TIMESTAMP'1900-01-01', TIMESTAMP'9999-12-31', true, 'UNKNOWN'
WHERE NOT EXISTS (SELECT 1 FROM ${catalog}.gold.dim_machine WHERE machine_sk = -1);

INSERT INTO ${catalog}.gold.dim_product (product_sk, product_id, sku, product_name, product_family, effective_from, effective_to, is_current, attr_hash)
SELECT -1, 'UNKNOWN', 'UNKNOWN', 'Unknown product', 'UNKNOWN', TIMESTAMP'1900-01-01', TIMESTAMP'9999-12-31', true, 'UNKNOWN'
WHERE NOT EXISTS (SELECT 1 FROM ${catalog}.gold.dim_product WHERE product_sk = -1);

INSERT INTO ${catalog}.gold.dim_plant (plant_sk, plant_id, plant_name)
SELECT -1, 'UNKNOWN', 'Unknown plant' WHERE NOT EXISTS (SELECT 1 FROM ${catalog}.gold.dim_plant WHERE plant_sk = -1);

INSERT INTO ${catalog}.gold.dim_supplier (supplier_sk, supplier_id, supplier_name)
SELECT -1, 'UNKNOWN', 'Unknown supplier' WHERE NOT EXISTS (SELECT 1 FROM ${catalog}.gold.dim_supplier WHERE supplier_sk = -1);

/* =============================================================================
   SEED: one row per source entity. ADDING A NEW SOURCE TABLE = ADD A ROW HERE
   (+ its block in configs/source_registry.yml). No pipeline change.

   Tokens rendered by ctl.usp_start_ingestion_run: {window_start} {window_end}
   Every hwm_query returns a column named HWM; every count query returns SOURCE_COUNT.
   ============================================================================= */

/* Fresh-environment seed. Once runs exist (FK from run history), change rows with
   UPDATE statements through a reviewed PR instead of re-seeding. */
IF EXISTS (SELECT 1 FROM ctl.ingestion_run_history)
    THROW 50030, 'Control DB already has run history: apply incremental UPDATEs instead of re-seeding', 1;
DELETE FROM ctl.ingestion_control;
GO

/* ---------------------------------------------------------------------------
   SQL SERVER MES — native Change Data Capture
   Window = (last extracted LSN, current max LSN]. First run starts at the
   capture instance's min LSN. If the CDC cleanup job already purged changes we
   have not extracted (retention default 3 days), THROW -> fail loudly: a silent
   gap would mean lost updates. Recovery = full reload + usp_reset_watermark.
   --------------------------------------------------------------------------- */
DECLARE @cdc_template NVARCHAR(MAX) = N'
DECLARE @min_lsn BINARY(10) = sys.fn_cdc_get_min_lsn(''<CI>'');
DECLARE @from_lsn BINARY(10) = CASE WHEN ''{window_start}'' = '''' THEN @min_lsn
                                    ELSE sys.fn_cdc_increment_lsn(CONVERT(BINARY(10), ''{window_start}'', 1)) END;
DECLARE @to_lsn BINARY(10) = CONVERT(BINARY(10), ''{window_end}'', 1);
IF @from_lsn < @min_lsn
    THROW 50001, ''CDC LSN GAP for <CI>: changes purged before extraction. Full reload + watermark reset required.'', 1;
SELECT CONVERT(VARCHAR(22), __$start_lsn, 1)    AS cdc_start_lsn,
       CONVERT(VARCHAR(22), __$seqval, 1)       AS cdc_seqval,
       __$operation                              AS cdc_operation,   -- 1 delete, 2 insert, 4 update (after image)
       CONVERT(VARCHAR(130), __$update_mask, 1) AS cdc_update_mask,
       sys.fn_cdc_map_lsn_to_time(__$start_lsn)  AS cdc_commit_ts,
       <COLS>
FROM cdc.fn_cdc_get_all_changes_<CI>(@from_lsn, @to_lsn, N''all'');';

DECLARE @cdc_count NVARCHAR(MAX) = N'
DECLARE @min_lsn BINARY(10) = sys.fn_cdc_get_min_lsn(''<CI>'');
DECLARE @from_lsn BINARY(10) = CASE WHEN ''{window_start}'' = '''' THEN @min_lsn
                                    ELSE sys.fn_cdc_increment_lsn(CONVERT(BINARY(10), ''{window_start}'', 1)) END;
IF @from_lsn < @min_lsn
    THROW 50001, ''CDC LSN GAP for <CI>: changes purged before extraction. Full reload + watermark reset required.'', 1;
SELECT COUNT_BIG(*) AS SOURCE_COUNT FROM cdc.fn_cdc_get_all_changes_<CI>(@from_lsn, CONVERT(BINARY(10), ''{window_end}'', 1), N''all'');';

DECLARE @cdc_hwm NVARCHAR(MAX) = N'SELECT CONVERT(VARCHAR(22), sys.fn_cdc_get_max_lsn(), 1) AS HWM;';

INSERT INTO ctl.ingestion_control (entity_name, source_system, source_type, batch_group, priority, source_object, cdc_capture_instance,
    watermark_column, watermark_type, initial_watermark_value, hwm_query, source_query_template, count_query_template, target_folder, owner_team, notes)
VALUES
('mes_machine', 'SQLSERVER_MES', 'SQLSERVER_CDC', 'DAILY', 10, 'dbo.machine', 'dbo_machine', '__$start_lsn', 'LSN', '', @cdc_hwm,
  REPLACE(REPLACE(@cdc_template, '<CI>', 'dbo_machine'), '<COLS>',
    'machine_id, machine_name, plant_id, line_id, machine_type, manufacturer, install_date, status, rated_units_per_hour, modified_at'),
  REPLACE(@cdc_count, '<CI>', 'dbo_machine'), 'sqlserver_mes/machine', 'Manufacturing IT', 'Machine master; drives dim_machine SCD2'),

('mes_production_log', 'SQLSERVER_MES', 'SQLSERVER_CDC', 'HOURLY', 20, 'dbo.production_log', 'dbo_production_log', '__$start_lsn', 'LSN', '', @cdc_hwm,
  REPLACE(REPLACE(@cdc_template, '<CI>', 'dbo_production_log'), '<COLS>',
    'production_log_id, order_id, machine_id, product_id, shift_code, start_ts, end_ts, units_produced, units_scrapped, operator_id, modified_at'),
  REPLACE(@cdc_count, '<CI>', 'dbo_production_log'), 'sqlserver_mes/production_log', 'Manufacturing IT', 'Production runs; hourly for near-real-time shop-floor KPIs'),

('mes_quality_inspection', 'SQLSERVER_MES', 'SQLSERVER_CDC', 'HOURLY', 30, 'dbo.quality_inspection', 'dbo_quality_inspection', '__$start_lsn', 'LSN', '', @cdc_hwm,
  REPLACE(REPLACE(@cdc_template, '<CI>', 'dbo_quality_inspection'), '<COLS>',
    'inspection_id, production_log_id, machine_id, product_id, inspection_ts, sample_size, defect_count, defect_code, inspection_result, inspector_id, modified_at'),
  REPLACE(@cdc_count, '<CI>', 'dbo_quality_inspection'), 'sqlserver_mes/quality_inspection', 'Quality', 'QC inspections');

/* ---------------------------------------------------------------------------
   ORACLE ERP — query-based incremental on LAST_UPDATE_DATE (maintained by ERP triggers)
   Window = (last watermark, MAX(LAST_UPDATE_DATE) at start]. The upper bound is
   frozen, so rows committed DURING the copy are picked up next run, not lost.
   Hard deletes are NOT visible with this pattern (see docs/CDC_EXPLAINED.md):
   the ERP soft-deletes orders (ORDER_STATUS = 'CANCELLED'); plants are full-loaded.
   --------------------------------------------------------------------------- */
INSERT INTO ctl.ingestion_control (entity_name, source_system, source_type, batch_group, priority, source_object,
    watermark_column, watermark_type, initial_watermark_value, hwm_query, source_query_template, count_query_template, target_folder, owner_team, notes)
VALUES
('erp_plant', 'ORACLE_ERP', 'ORACLE_FULL', 'DAILY', 5, 'ERP.PLANTS', NULL, 'NONE', NULL, NULL,
  'SELECT PLANT_ID, PLANT_NAME, CITY, COUNTRY_CODE, TIMEZONE, LAST_UPDATE_DATE FROM ERP.PLANTS',
  'SELECT COUNT(*) AS SOURCE_COUNT FROM ERP.PLANTS', 'oracle_erp/plant', 'Finance IT', 'Tiny reference table: full load every day'),

('erp_product', 'ORACLE_ERP', 'ORACLE_WATERMARK', 'DAILY', 10, 'ERP.PRODUCTS', 'LAST_UPDATE_DATE', 'DATETIME', '1900-01-01 00:00:00',
  'SELECT TO_CHAR(MAX(LAST_UPDATE_DATE), ''YYYY-MM-DD HH24:MI:SS'') AS HWM FROM ERP.PRODUCTS',
  'SELECT PRODUCT_ID, SKU, PRODUCT_NAME, PRODUCT_FAMILY, UNIT_COST, STD_CYCLE_TIME_SEC, LAST_UPDATE_DATE FROM ERP.PRODUCTS
    WHERE LAST_UPDATE_DATE >  TO_DATE(''{window_start}'', ''YYYY-MM-DD HH24:MI:SS'')
      AND LAST_UPDATE_DATE <= TO_DATE(''{window_end}'',   ''YYYY-MM-DD HH24:MI:SS'')',
  'SELECT COUNT(*) AS SOURCE_COUNT FROM ERP.PRODUCTS
    WHERE LAST_UPDATE_DATE >  TO_DATE(''{window_start}'', ''YYYY-MM-DD HH24:MI:SS'')
      AND LAST_UPDATE_DATE <= TO_DATE(''{window_end}'',   ''YYYY-MM-DD HH24:MI:SS'')',
  'oracle_erp/product', 'Finance IT', 'Product master; drives dim_product SCD2'),

('erp_production_order', 'ORACLE_ERP', 'ORACLE_WATERMARK', 'DAILY', 40, 'ERP.PRODUCTION_ORDERS', 'LAST_UPDATE_DATE', 'DATETIME', '1900-01-01 00:00:00',
  'SELECT TO_CHAR(MAX(LAST_UPDATE_DATE), ''YYYY-MM-DD HH24:MI:SS'') AS HWM FROM ERP.PRODUCTION_ORDERS',
  'SELECT ORDER_ID, PRODUCT_ID, PLANT_ID, PLANNED_QTY, PLANNED_START, PLANNED_END, ORDER_STATUS, LAST_UPDATE_DATE FROM ERP.PRODUCTION_ORDERS
    WHERE LAST_UPDATE_DATE >  TO_DATE(''{window_start}'', ''YYYY-MM-DD HH24:MI:SS'')
      AND LAST_UPDATE_DATE <= TO_DATE(''{window_end}'',   ''YYYY-MM-DD HH24:MI:SS'')',
  'SELECT COUNT(*) AS SOURCE_COUNT FROM ERP.PRODUCTION_ORDERS
    WHERE LAST_UPDATE_DATE >  TO_DATE(''{window_start}'', ''YYYY-MM-DD HH24:MI:SS'')
      AND LAST_UPDATE_DATE <= TO_DATE(''{window_end}'',   ''YYYY-MM-DD HH24:MI:SS'')',
  'oracle_erp/production_order', 'Finance IT', 'Planned orders (plan vs actual)');

/* ---------------------------------------------------------------------------
   CMMS REST API — updated_since/updated_until window, paginated
   source_query_template = relative URL of the data call; count_query_template =
   the same filter with page_size=1 (the API returns meta.total_count).
   5-minute safety lag: the API's search index lags its database slightly.
   --------------------------------------------------------------------------- */
INSERT INTO ctl.ingestion_control (entity_name, source_system, source_type, batch_group, priority, source_object,
    watermark_column, watermark_type, initial_watermark_value, source_query_template, count_query_template, target_folder,
    api_safety_lag_minutes, owner_team, notes)
VALUES
('cmms_work_order', 'CMMS_API', 'REST_API', 'DAILY', 50, 'work-orders', 'updated_at', 'DATETIME', '1900-01-01T00:00:00Z',
  'work-orders?updated_since={window_start}&updated_until={window_end}&page_size=500',
  'work-orders?updated_since={window_start}&updated_until={window_end}&page_size=1',
  'cmms_api/work_order', 5, 'Maintenance', 'OAuth2 client credentials; secret in Key Vault');

/* ---------------------------------------------------------------------------
   SUPPLIER FILES — CSVs dropped into the ADLS `landing` container
   Window = file LastModified in (last watermark, trigger time]. Processed files
   are MOVED to landing/.../archive/. Up to 2% malformed rows are tolerated
   (logged to raw/_rejected/ by Copy fault tolerance); more fails the run.
   --------------------------------------------------------------------------- */
INSERT INTO ctl.ingestion_control (entity_name, source_system, source_type, batch_group, priority, source_object,
    watermark_column, watermark_type, initial_watermark_value, landing_folder, file_pattern, target_folder, max_reject_pct, owner_team, notes)
VALUES
('supplier_delivery', 'SUPPLIER_FILES', 'ADLS_FILE', 'DAILY', 60, 'supplier_deliveries', 'LastModified', 'DATETIME', '1900-01-01T00:00:00Z',
  'supplier_deliveries/incoming', '*_deliveries_*.csv', 'supplier_files/delivery', 2.00, 'Procurement', 'One CSV per supplier per day via SFTP->ADLS');
GO

SELECT entity_id, entity_name, source_type, batch_group, priority, watermark_type FROM ctl.ingestion_control ORDER BY priority;

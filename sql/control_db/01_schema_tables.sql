/* =============================================================================
   ADF CONTROL DATABASE (Azure SQL Database: sqldb-nf-mfg-control)

   The metadata that drives EVERY Azure Data Factory ingestion. ADF pipelines
   contain no table names, no queries and no watermarks: they read them from here.

   ctl.ingestion_control      WHAT to extract and HOW (one row per source entity)
   ctl.batch_run              one row per master pipeline execution
   ctl.ingestion_run_history  one row per entity per extraction window (+ attempts)
   ctl.watermark_history      every watermark movement, including manual resets
   ctl.audit_log              every significant event, who and when

   Access: ADF connects with its system-assigned MANAGED IDENTITY
   (CREATE USER [adf-nf-mfg-dev] FROM EXTERNAL PROVIDER) — no SQL passwords.
   ============================================================================= */

IF SCHEMA_ID('ctl') IS NULL EXEC('CREATE SCHEMA ctl');
GO

CREATE TABLE ctl.ingestion_control (
    entity_id               INT IDENTITY(1,1)  NOT NULL CONSTRAINT pk_ingestion_control PRIMARY KEY,
    entity_name             VARCHAR(100)       NOT NULL CONSTRAINT uq_ingestion_control_entity UNIQUE,  -- == configs/source_registry.yml key
    source_system           VARCHAR(50)        NOT NULL,   -- SQLSERVER_MES | ORACLE_ERP | CMMS_API | SUPPLIER_FILES
    source_type             VARCHAR(30)        NOT NULL    -- routes to the child pipeline (Switch activity)
        CONSTRAINT ck_source_type CHECK (source_type IN ('SQLSERVER_CDC','ORACLE_WATERMARK','ORACLE_FULL','REST_API','ADLS_FILE')),
    batch_group             VARCHAR(20)        NOT NULL,   -- DAILY | HOURLY: which trigger picks it up
    priority                INT                NOT NULL DEFAULT 100,  -- lower = earlier (masters before transactions)
    is_active               BIT                NOT NULL DEFAULT 1,
    source_object           VARCHAR(200)       NOT NULL,   -- dbo.production_log | ERP.PRODUCTS | work-orders | landing folder
    cdc_capture_instance    VARCHAR(128)       NULL,
    watermark_column        VARCHAR(128)       NULL,
    watermark_type          VARCHAR(20)        NOT NULL    -- LSN | DATETIME | NONE (full load)
        CONSTRAINT ck_watermark_type CHECK (watermark_type IN ('LSN','DATETIME','NONE')),
    initial_watermark_value VARCHAR(100)       NULL,       -- what a reset goes back to
    last_watermark_value    VARCHAR(100)       NULL,       -- advanced ONLY by usp_complete_ingestion_run
    hwm_query               NVARCHAR(MAX)      NULL,       -- returns column HWM = candidate window end
    source_query_template   NVARCHAR(MAX)      NULL,       -- tokens: {window_start} {window_end}; REST: relative URL
    count_query_template    NVARCHAR(MAX)      NULL,       -- independent source count for reconciliation
    landing_folder          VARCHAR(500)       NULL,       -- ADLS_FILE: where suppliers drop files
    file_pattern            VARCHAR(100)       NULL,
    target_container        VARCHAR(50)        NOT NULL DEFAULT 'raw',
    target_folder           VARCHAR(500)       NOT NULL,   -- == source_registry raw_path
    max_reject_pct          DECIMAL(5,2)       NOT NULL DEFAULT 0,     -- tolerated bad rows (files only)
    api_safety_lag_minutes  INT                NOT NULL DEFAULT 0,     -- REST: don't read the last N minutes (late commits)
    alert_severity          VARCHAR(10)        NOT NULL DEFAULT 'HIGH',
    owner_team              VARCHAR(100)       NOT NULL,
    notes                   NVARCHAR(1000)     NULL,
    created_ts              DATETIME2(3)       NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_ts              DATETIME2(3)       NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE ctl.batch_run (
    batch_run_id         BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_batch_run PRIMARY KEY,
    batch_group          VARCHAR(20)   NOT NULL,
    run_date             DATE          NOT NULL,
    adf_pipeline_run_id  VARCHAR(100)  NOT NULL,
    status               VARCHAR(20)   NOT NULL,  -- RUNNING | SUCCESS | PARTIAL_FAILURE | FAILED
    entities_total       INT           NULL,
    entities_succeeded   INT           NULL,
    entities_failed      INT           NULL,
    start_ts             DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    end_ts               DATETIME2(3)  NULL
);
GO

CREATE TABLE ctl.ingestion_run_history (
    run_id               VARCHAR(120)  NOT NULL CONSTRAINT pk_ingestion_run_history PRIMARY KEY, -- <entity>_<yyyymmdd>_<nn>
    entity_id            INT           NOT NULL CONSTRAINT fk_irh_entity REFERENCES ctl.ingestion_control(entity_id),
    entity_name          VARCHAR(100)  NOT NULL,
    batch_run_id         BIGINT        NULL,
    adf_pipeline_run_id  VARCHAR(100)  NULL,      -- the LATEST attempt's ADF run id
    attempt_no           INT           NOT NULL DEFAULT 1,
    load_date            DATE          NOT NULL,
    window_start         VARCHAR(100)  NULL,      -- exclusive
    window_end           VARCHAR(100)  NULL,      -- inclusive; FROZEN at attempt 1 so retries are replayable
    source_query         NVARCHAR(MAX) NULL,      -- rendered query actually executed (audit + replay)
    count_query          NVARCHAR(MAX) NULL,
    target_path          VARCHAR(500)  NULL,
    source_count         BIGINT        NULL,
    rows_read            BIGINT        NULL,
    rows_copied          BIGINT        NULL,
    rows_skipped         BIGINT        NULL,
    status               VARCHAR(20)   NOT NULL,  -- STARTED | VALIDATED | SUCCESS | NO_CHANGES | FAILED | RECON_FAILED
    error_message        NVARCHAR(4000) NULL,
    start_ts             DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    end_ts               DATETIME2(3)  NULL
);
CREATE INDEX ix_irh_entity_status ON ctl.ingestion_run_history(entity_id, status) INCLUDE (load_date, window_end);
GO

CREATE TABLE ctl.watermark_history (
    watermark_history_id BIGINT IDENTITY(1,1) CONSTRAINT pk_watermark_history PRIMARY KEY,
    entity_id    INT           NOT NULL,
    old_value    VARCHAR(100)  NULL,
    new_value    VARCHAR(100)  NULL,
    run_id       VARCHAR(120)  NULL,     -- NULL for a manual reset
    change_type  VARCHAR(20)   NOT NULL, -- ADVANCE | MANUAL_RESET
    reason       NVARCHAR(500) NULL,
    changed_by   SYSNAME       NOT NULL DEFAULT SUSER_SNAME(),
    changed_ts   DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

CREATE TABLE ctl.audit_log (
    audit_id      BIGINT IDENTITY(1,1) CONSTRAINT pk_audit_log PRIMARY KEY,
    run_id        VARCHAR(120)   NULL,
    entity_name   VARCHAR(100)   NULL,
    event_type    VARCHAR(50)    NOT NULL,  -- RUN_STARTED | RUN_RETRY | COUNTS_VALIDATED | RECON_FAILED | WATERMARK_ADVANCED | RUN_FAILED | ...
    event_detail  NVARCHAR(MAX)  NULL,      -- JSON
    created_by    SYSNAME        NOT NULL DEFAULT SUSER_SNAME(),
    created_ts    DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

/* On-call view: what happened today, per entity */
CREATE OR ALTER VIEW ctl.vw_today_ingestion_status AS
SELECT c.entity_name, c.source_system, c.source_type, c.batch_group, c.last_watermark_value,
       h.run_id, h.attempt_no, h.status, h.source_count, h.rows_read, h.rows_copied, h.rows_skipped,
       h.error_message, h.start_ts, h.end_ts
FROM ctl.ingestion_control c
OUTER APPLY (SELECT TOP 1 * FROM ctl.ingestion_run_history r
             WHERE r.entity_id = c.entity_id AND r.load_date = CAST(SYSUTCDATETIME() AS DATE)
             ORDER BY r.start_ts DESC) h
WHERE c.is_active = 1;
GO

/* Exactly what ADF writes to raw/_manifests/<entity>/<run_id>.json (Write_Manifest activity).
   Databricks reconciles bronze row counts against rows_copied. */
CREATE OR ALTER VIEW ctl.vw_run_manifest AS
SELECT h.run_id, h.entity_name, c.source_system, c.source_type AS load_pattern,
       CONVERT(VARCHAR(10), h.load_date, 23) AS load_date, h.window_start, h.window_end,
       h.source_count, h.rows_read, h.rows_copied, h.rows_skipped, h.status, h.attempt_no,
       h.adf_pipeline_run_id, CONVERT(VARCHAR(33), SYSUTCDATETIME(), 127) AS manifest_written_utc
FROM ctl.ingestion_run_history h
JOIN ctl.ingestion_control c ON c.entity_id = h.entity_id;
GO

/* =============================================================================
   SOURCE: on-prem SQL Server 2019 — MES database (Manufacturing Execution System)
   Shows the source tables and HOW CDC IS ENABLED. Run by the source DBA, not by us.
   ============================================================================= */

USE MES_PROD;
GO

CREATE TABLE dbo.machine (
    machine_id            VARCHAR(20)   NOT NULL PRIMARY KEY,
    machine_name          NVARCHAR(100) NOT NULL,
    plant_id              VARCHAR(10)   NOT NULL,
    line_id               VARCHAR(20)   NOT NULL,
    machine_type          VARCHAR(30)   NULL,
    manufacturer          NVARCHAR(50)  NULL,
    install_date          DATE          NULL,
    status                VARCHAR(20)   NOT NULL,
    rated_units_per_hour  INT           NULL,
    modified_at           DATETIME2(0)  NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE dbo.production_log (
    production_log_id  BIGINT IDENTITY(100001,1) PRIMARY KEY,
    order_id           VARCHAR(40)  NOT NULL,
    machine_id         VARCHAR(20)  NOT NULL,
    product_id         VARCHAR(20)  NOT NULL,
    shift_code         CHAR(1)      NULL,
    start_ts           DATETIME2(0) NOT NULL,
    end_ts             DATETIME2(0) NULL,
    units_produced     INT          NOT NULL,
    units_scrapped     INT          NULL,
    operator_id        VARCHAR(20)  NULL,
    modified_at        DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE dbo.quality_inspection (
    inspection_id      BIGINT IDENTITY(500001,1) PRIMARY KEY,
    production_log_id  BIGINT       NOT NULL,
    machine_id         VARCHAR(20)  NOT NULL,
    product_id         VARCHAR(20)  NOT NULL,
    inspection_ts      DATETIME2(0) NOT NULL,
    sample_size        INT          NOT NULL,
    defect_count       INT          NOT NULL,
    defect_code        VARCHAR(30)  NULL,
    inspection_result  VARCHAR(10)  NOT NULL,
    inspector_id       VARCHAR(20)  NULL,
    modified_at        DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

/* ---- 1. enable CDC on the database (requires sysadmin). SQL Server Agent must be running:
          CDC is populated by the capture job reading the transaction log. */
EXEC sys.sp_cdc_enable_db;
GO

/* ---- 2. enable CDC per table. @role_name gates who can read change data:
          only the ADF extraction login is a member of cdc_reader. */
CREATE ROLE cdc_reader;
EXEC sys.sp_cdc_enable_table @source_schema = N'dbo', @source_name = N'machine',            @role_name = N'cdc_reader', @supports_net_changes = 1;
EXEC sys.sp_cdc_enable_table @source_schema = N'dbo', @source_name = N'production_log',     @role_name = N'cdc_reader', @supports_net_changes = 1;
EXEC sys.sp_cdc_enable_table @source_schema = N'dbo', @source_name = N'quality_inspection', @role_name = N'cdc_reader', @supports_net_changes = 1;
GO
-- creates change tables cdc.dbo_machine_CT, cdc.dbo_production_log_CT, cdc.dbo_quality_inspection_CT
-- and functions cdc.fn_cdc_get_all_changes_dbo_<table> / cdc.fn_cdc_get_net_changes_dbo_<table>

/* ---- 3. retention: keep 7 days of change data (default 3). Our window is hourly/daily,
          so 7 days lets us survive a long outage without an LSN gap. */
EXEC sys.sp_cdc_change_job @job_type = N'cleanup', @retention = 10080;  -- minutes
GO

/* ---- 4. least-privilege extraction login used by ADF (password lives in Azure Key Vault) */
CREATE LOGIN svc_adf_mes WITH PASSWORD = '<set-by-dba-stored-in-key-vault>';
CREATE USER svc_adf_mes FOR LOGIN svc_adf_mes;
ALTER ROLE cdc_reader ADD MEMBER svc_adf_mes;
GRANT SELECT ON SCHEMA::cdc TO svc_adf_mes;
GRANT SELECT ON dbo.machine TO svc_adf_mes;   -- initial snapshot / full reload only
GO

/* ---- 5. what a CDC read looks like (this is what ADF runs, rendered from the control table) */
DECLARE @from_lsn BINARY(10) = sys.fn_cdc_get_min_lsn('dbo_production_log');
DECLARE @to_lsn   BINARY(10) = sys.fn_cdc_get_max_lsn();
SELECT __$start_lsn, __$seqval, __$operation, __$update_mask, *
FROM cdc.fn_cdc_get_all_changes_dbo_production_log(@from_lsn, @to_lsn, N'all update old');
-- __$operation: 1 = delete, 2 = insert, 3 = update BEFORE image, 4 = update AFTER image.
-- 'all' returns 1/2/4 only (what we extract); 'all update old' also returns 3. See sample_data/cdc_explained/.

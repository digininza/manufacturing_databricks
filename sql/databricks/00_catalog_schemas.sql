-- =============================================================================
-- Unity Catalog setup. ${catalog} is substituted by notebooks/00_setup/00_create_catalog_schemas.py
-- Storage is reached through EXTERNAL LOCATIONS backed by a STORAGE CREDENTIAL
-- (the Azure Databricks Access Connector's managed identity). No account keys,
-- no SAS tokens, no service-principal secrets in Spark config.
-- =============================================================================

-- One-time, by a metastore admin (shown for completeness):
-- CREATE STORAGE CREDENTIAL IF NOT EXISTS sc_nf_mfg_access_connector
--   WITH (AZURE_MANAGED_IDENTITY ( ACCESS_CONNECTOR_ID = '/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Databricks/accessConnectors/ac-nf-mfg' ));
-- CREATE EXTERNAL LOCATION IF NOT EXISTS el_nf_mfg_raw
--   URL 'abfss://raw@stnfmfg${env}.dfs.core.windows.net/' WITH (STORAGE CREDENTIAL sc_nf_mfg_access_connector);
-- CREATE EXTERNAL LOCATION IF NOT EXISTS el_nf_mfg_checkpoints
--   URL 'abfss://checkpoints@stnfmfg${env}.dfs.core.windows.net/' WITH (STORAGE CREDENTIAL sc_nf_mfg_access_connector);
-- GRANT READ FILES ON EXTERNAL LOCATION el_nf_mfg_raw TO `sp-nf-mfg-databricks-jobs`;
-- GRANT READ FILES, WRITE FILES ON EXTERNAL LOCATION el_nf_mfg_checkpoints TO `sp-nf-mfg-databricks-jobs`;

CREATE CATALOG IF NOT EXISTS ${catalog} COMMENT 'NorthForge manufacturing modernization platform';

CREATE SCHEMA IF NOT EXISTS ${catalog}.control    COMMENT 'Run log, audit, DQ results, reconciliation, watermarks';
CREATE SCHEMA IF NOT EXISTS ${catalog}.bronze     COMMENT 'Raw, append-only. As landed by ADF (Auto Loader) and Event Hub';
CREATE SCHEMA IF NOT EXISTS ${catalog}.silver     COMMENT 'Cleansed, deduplicated, CDC-applied, conformed';
CREATE SCHEMA IF NOT EXISTS ${catalog}.quarantine COMMENT 'Rows rejected by ERROR-severity DQ rules';
CREATE SCHEMA IF NOT EXISTS ${catalog}.gold       COMMENT 'Star schema: facts + SCD2 dimensions. Published to Snowflake';

-- Least privilege: engineers read everything in dev; only the job service principal writes.
GRANT USE CATALOG ON CATALOG ${catalog} TO `grp-nf-mfg-data-engineers`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.gold TO `grp-nf-mfg-data-analysts`;
GRANT USE SCHEMA, SELECT ON SCHEMA ${catalog}.control TO `grp-nf-mfg-data-engineers`;
GRANT ALL PRIVILEGES ON CATALOG ${catalog} TO `sp-nf-mfg-databricks-jobs`;

# 06 · Low-level design / technical design document (LLD / TDD)

| Doc ID | NFMFG-DOC-06-<epic> | Owner | Senior data engineer (per epic) | Reviewers | Peer engineers | Approver | Tech lead / architect |
|---|---|---|---|---|---|---|---|

One LLD page **per epic** (Ingestion, Silver, Gold, Streaming, Snowflake publish, Power BI).
The LLD is precise enough that another engineer could build it without asking the author.

## Template: sections per LLD
| § | Section | Example (epic "Ingestion — MES CDC") |
|---|---|---|
| 1 | Scope & linked docs | ICD §1 (03), STTM pages (07), Jira epic NFMFG-10 |
| 2 | Component list | `PL_00`, `PL_01`, `PL_10`; control DB SPs; datasets `DS_SqlServer_MES`, `DS_ADLS_Parquet` |
| 3 | Control metadata | `ctl.ingestion_control` rows (exact values), `hwm_query`, query templates |
| 4 | Processing logic, step by step | Get HWM → start run (frozen window) → count → copy to `_staging` → validate → publish → manifest → advance watermark |
| 5 | Objects created | ADLS paths `raw/sqlserver_mes/<entity>/load_date=/run_id=`; manifest path |
| 6 | Parameters & configuration | Pipeline parameters, global parameters, per-environment values |
| 7 | Error handling & retry | Retry 3 × 120s on Copy/Lookup; no retry on validation; catch → `usp_fail_ingestion_run` → alert → Fail |
| 8 | Idempotency & restart | Run-id reuse, deterministic file names, rerun-from-failure |
| 9 | Reconciliation & DQ | source_count = rows_read = rows_copied + skipped |
| 10 | Performance | Parallelism (ForEach batch 4), SHIR sizing, partitioned reads for large tables |
| 11 | Security | Key Vault references, MI, `secureOutput` |
| 12 | Logging, monitoring, alerts | Control tables, Azure Monitor rules, Logic App |
| 13 | Test cases | Links to doc 11 test IDs |
| 14 | Deployment notes | ARM parameters, trigger stop/start |

## Current LLD content in this repo (link these from Confluence)
| Epic | LLD source |
|---|---|
| Ingestion (ADF) | `docs/ADF_FRAMEWORK.md`, `adf/`, `sql/control_db/` |
| Bronze / Silver | `docs/DATA_QUALITY_AND_RECONCILIATION.md`, `src/bronze/`, `src/silver/`, `configs/source_registry.yml` |
| Gold | `docs/DATA_MODEL.md`, `src/gold/`, `sql/databricks/02_gold_tables.sql` |
| Streaming | `docs/STREAMING.md`, `src/streaming/` |
| Performance | `docs/PERFORMANCE_AND_SCALE.md` |
| Snowflake & Power BI | `docs/POWERBI_AND_SNOWFLAKE.md`, `sql/snowflake/`, `powerbi/` |

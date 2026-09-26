# Developer standards (the automation framework every engineer follows)

The framework makes the right thing the default. A developer onboarding a source does
**not** write extraction pipelines, watermark logic, audit logging, DQ plumbing or
reconciliation. They declare metadata, and the framework does the rest the same way for
every table.

## 1. Onboarding a new source table (for example, MES `dbo.scrap_reason`)

| Step | File | What you add |
|---|---|---|
| 1 | Source DBA | `sys.sp_cdc_enable_table` for the table (SQL Server), or confirm there is an indexed `LAST_UPDATE_DATE` (Oracle) |
| 2 | `sql/control_db/` (a reviewed change script) | One `INSERT INTO ctl.ingestion_control`: `entity_name`, `source_type`, `batch_group`, `source_query_template`, `count_query_template`, `hwm_query`, `target_folder`, `owner_team` |
| 3 | `configs/source_registry.yml` | One entity block: `raw_path` (= `target_folder`), `primary_keys`, `sequence_by`, `columns` (type + transforms), `null_defaults`, `dq_rules` |
| 4 | `sql/databricks/02_gold_tables.sql` + `src/gold/` | Only if the entity feeds a new dimension or fact |
| 5 | PR | CI runs `tests/test_config_registry.py`, which fails if the control table and the registry disagree |

No new ADF pipeline. No new notebook.

## 2. Mandatory standards

### Incremental ingestion
- Every entity declares a `watermark_type`: `LSN` (CDC), `DATETIME` (column, API or file time) or `NONE` (full load, only for tables under 100k rows).
- Watermarks are **only** advanced by `ctl.usp_complete_ingestion_run` (ADF) or `advance_version_watermark` (Databricks), and **only after reconciliation passes**.
- Windows are `(start, end]`, and `end` is **frozen at the first attempt**.
- Oracle and API sources must document their hard-delete strategy.
- Manual watermark changes go through `ctl.usp_reset_watermark` with a ticket number. They are audited in `ctl.watermark_history`.

### Control, history and audit tables
| Table | Written by | Answers |
|---|---|---|
| `ctl.ingestion_control` | Engineers (via PR) | What is extracted, how, and who owns it |
| `ctl.ingestion_run_history` | ADF SPs | What happened to each window: counts, attempts, errors |
| `ctl.audit_log`, `ctl.watermark_history` | ADF SPs | Who changed what, and when |
| `control.pipeline_run_log` | Databricks framework | Rows in / valid / quarantined / duplicate / written per entity and batch |
| `control.dq_results` | DQ engine | Failures per rule per batch (trend dashboards) |
| `control.reconciliation_results` | Reconciliation framework | Every check at every hop |
| `control.audit_log` | `AuditLogger` | Significant events: job start/end, publish, refresh |

### Idempotency
- Every write in silver, gold and Snowflake is a **MERGE on business keys**. There is no blind `append` except for append-only logs.
- Streaming side-writes (quarantine, DQ results) use `txnAppId` + `txnVersion = batch_id`.
- File names are deterministic (`<run_id>.parquet`).

### Data quality
- At least one `ERROR` rule on every primary key (`NOT NULL`).
- Numeric domain rules (non-negative, bounded) are `ERROR`. Descriptive and domain-list rules are usually `WARN`.
- A rule's SQL must be written so that NULL means "failed" (the engine enforces this).

### Naming
| Object | Convention | Example |
|---|---|---|
| Entity | `<system>_<table>` snake_case | `mes_production_log` |
| ADF pipeline | `PL_<nn>_<Verb>_<What>` | `PL_10_Ingest_SqlServer_CDC` |
| ADF dataset / linked service | `DS_` / `LS_` + technology | `DS_ADLS_Parquet` |
| Framework columns | leading underscore | `_ingestion_run_id`, `_is_deleted` |
| Gold | `dim_` / `fact_` + grain in the table comment | `fact_machine_daily` |
| Snowflake | UPPER_CASE, `VW_` for views | `REPORTING.VW_PLANT_OEE_DAILY` |

### Code
- Business rules live in config (registry, control table). Code stays generic.
- No secrets anywhere in the repo. Use `get_secret(scope, key)` / Key Vault references.
- `black` (line length 160) and `flake8` must pass. Pure logic (SQL builders, recon rules) gets unit tests.
- Notebooks are thin: parameters, a call into `src/`, and a display. Logic lives in `src/`.

## 3. PR checklist
- [ ] The control-table row and the registry block agree (CI enforces this)
- [ ] Primary key and sequence are correct; the delete strategy is documented
- [ ] DQ rules added, with severities justified
- [ ] If the entity feeds gold: the SILVER_TO_GOLD reconciliation is extended
- [ ] Runbook / troubleshooting updated if new failure modes were introduced

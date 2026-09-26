# Runbook: deploy and operate

## A. First-time setup (per environment)

### 1. Azure resources (created by the platform team / IaC)
| Resource | Name (dev) | Notes |
|---|---|---|
| ADLS Gen2 | `stnfmfgdev` | Containers: `landing`, `raw`, `checkpoints` |
| Key Vault | `kv-nf-mfg-dev` | Secrets listed in [docs/SECURITY.md](docs/SECURITY.md) |
| Azure SQL DB | `sql-nf-mfg-dev` / `sqldb-nf-mfg-control` | Entra-only auth |
| Data Factory | `adf-nf-mfg-dev` | System-assigned MI enabled |
| Self-hosted IR | 2 VMs in the plant network | Register both nodes to `IR-SelfHosted-OnPrem` |
| Event Hub | `evhns-nf-mfg-dev` / `machine-telemetry` | 32 partitions, 7-day retention, consumer group `databricks-bronze`, Listen + Send SAS policies |
| Databricks | Premium, Unity Catalog, VNet-injected | Access Connector `ac-nf-mfg` |
| Logic App | `la-nf-mfg-alerts` | HTTP trigger, then Outlook + Teams |

### 2. Control DB
Run in order: `sql/control_db/01_schema_tables.sql`, `02_stored_procedures.sql`, `03_seed_ingestion_control.sql`.
Then create the ADF managed-identity user (see [docs/ADF_FRAMEWORK.md](docs/ADF_FRAMEWORK.md) §5).

### 3. Sources (source DBAs)
`sql/source_systems/sqlserver_mes_setup_and_cdc.sql` (CDC + read-only login) and `oracle_erp_setup.sql` (triggers, index, read-only user). Store both passwords in Key Vault.

### 4. ADF
Connect ADF Studio to this repo with root folder `/adf`. Replace the placeholder hosts in the linked services and global parameters, then **Publish**. Leave the triggers stopped for now.

### 5. Databricks
```bash
databricks secrets create-scope kv-nf-mfg-dev --scope-backend-type AZURE_KEYVAULT \
  --resource-id <key-vault-resource-id> --dns-name https://kv-nf-mfg-dev.vault.azure.net/
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev mfg_setup_and_demo_data     # catalog, schemas, control + gold tables (+ day-1 demo data)
```
Metastore admin, one time: the storage credential and external locations shown in `sql/databricks/00_catalog_schemas.sql`.
Add the ADF managed identity to the workspace as a service principal and grant it *CAN_MANAGE_RUN* on `mfg_batch_pipeline`. Put that job's ID in the ADF global parameter `gp_databricks_job_id`.

### 6. Snowflake
Run `sql/snowflake/01…04` as SYSADMIN/SECURITYADMIN. Generate RSA key pairs for the two service users; put the private keys in Key Vault and the public keys in `ALTER USER … SET RSA_PUBLIC_KEY`.

### 7. Power BI
Open `powerbi/NorthForge_Manufacturing.pbip`, set the parameters, and publish to the Dev workspace. Configure:
- the DirectQuery data source to use SSO;
- the Import credentials to use the `SVC_POWERBI_REFRESH` key pair;
- RLS role members (Entra groups).

Put the workspace and dataset IDs in `databricks.yml` variables.

### 8. Go live
Start `TR_Daily_0200_UTC` and `TR_Hourly_MES_Tumbling`, and unpause `mfg_iot_streaming`.

## B. Demo run without ADF or on-prem sources
1. `databricks bundle run -t dev mfg_setup_and_demo_data --params day=1`, then `databricks bundle run -t dev mfg_batch_pipeline`.
2. `databricks bundle run -t dev mfg_setup_and_demo_data --params day=2`, then run the batch pipeline again.
3. Open `notebooks/07_demos/*` (CDC, SCD2, reconciliation failure, idempotent rerun).

Skip the Snowflake and Power BI tasks if those aren't configured: run the notebooks up to `04_gold` by hand.

## C. Daily operations
| Time (UTC) | What | Where to look |
|---|---|---|
| Hourly :05 | MES CDC window | ADF Monitor → `TR_Hourly_MES_Tumbling` |
| 02:00 | Daily batch → Databricks → Snowflake → Power BI | ADF Monitor; `ctl.vw_today_ingestion_status` |
| 06:00 | SLA: Power BI refreshed | Power BI refresh history; "Data As Of" |
| Continuous | IoT stream | Job `mfg_iot_streaming`, backlog health rule |

**Morning checks** (control DB, then Databricks):
```sql
SELECT * FROM ctl.vw_today_ingestion_status WHERE status NOT IN ('SUCCESS','NO_CHANGES');
SELECT * FROM mfg_prod.control.vw_failed_reconciliations WHERE checked_ts >= current_date();
SELECT * FROM mfg_prod.control.vw_latest_entity_status WHERE status <> 'SUCCESS';
```

## D. Procedures
| Situation | Do this |
|---|---|
| **Entity failed, cause fixed** | Re-run `PL_00_Master_Orchestrator` (same `batch_group`, `run_date`). Only pending or failed entities run. |
| **Force re-pull of an entity already successful today** | Run `PL_00` with `force_rerun = true`, or run only that entity by temporarily setting `is_active = 0` on the others (the audit trail keeps this visible). |
| **Replay history / backfill a period** | `EXEC ctl.usp_reset_watermark '<entity>', '<value before the period>', '<ticket + reason>'`, then run `PL_00`. Silver's sequence guard makes overlapping data harmless. Gold recomputes the affected dates via CDF. |
| **CDC LSN gap** | See [TROUBLESHOOTING #3](docs/TROUBLESHOOTING.md) |
| **Rebuild a gold fact fully** | `DELETE FROM control.watermarks WHERE consumer='gold_facts'`, then run `gold_facts` (no watermark means a full rebuild). |
| **Re-publish a table to Snowflake fully** | `DELETE FROM control.watermarks WHERE consumer='snowflake_publish' AND source_table='mfg_prod.gold.<t>'` |
| **Pause everything for source maintenance** | Stop the ADF triggers; the tumbling window catches up afterwards, in order. |

# Security and credentials

**Principle:** no human passwords and no secrets in code. Azure resources authenticate with
**managed identities**. Anything that cannot use one (on-prem databases, a SaaS API, Snowflake,
webhooks) has its secret in **Azure Key Vault**, and code references the secret *name* only.

## 1. Identities

| Identity | Type | Used by | Access |
|---|---|---|---|
| `adf-nf-mfg-<env>` | ADF system-assigned managed identity | ADF | ADLS: *Storage Blob Data Contributor* on `raw` and `landing`. Key Vault: *Key Vault Secrets User*. Control DB: contained user (`db_datareader`, `db_datawriter`, EXECUTE on `ctl`). Databricks: added as a service principal with *CAN_MANAGE_RUN* on the batch job. |
| `ac-nf-mfg` | Databricks Access Connector (managed identity) | Unity Catalog storage credential | ADLS: *Storage Blob Data Contributor*, surfaced via **external locations** |
| `sp-nf-mfg-databricks-jobs` | Entra service principal | `run_as` for prod/test jobs | Unity Catalog grants on `mfg_<env>`, and the Key Vault-backed secret scope |
| `SVC_DATABRICKS_PUBLISHER` | Snowflake service user, **key-pair** | Snowflake publisher | `MFG_PUBLISHER_ROLE`: STAGE create/write, GOLD DML. Network policy limits it to the Databricks egress IPs. |
| `SVC_POWERBI_REFRESH` | Snowflake service user, key-pair | Power BI Import refresh | `MFG_BI_READER_ROLE`: read GOLD, REPORTING and the entitlements |
| End users | Entra ID | Power BI DirectQuery with **SSO** | Snowflake row access policy by plant |
| `svc_adf_mes` / `SVC_ADF_ERP` | Source DB logins | Self-hosted IR extraction | Read-only; `cdc_reader` role on SQL Server |

## 2. Key Vault secret inventory (`kv-nf-mfg-<env>`)

| Secret | Consumer |
|---|---|
| `mes-sqlserver-password` | ADF `LS_SqlServer_MES` |
| `erp-oracle-password` | ADF `LS_Oracle_ERP` |
| `cmms-client-secret` | ADF `PL_12` (OAuth2 client credentials) |
| `logicapp-alert-webhook-url` | ADF `PL_90`, Databricks `alerting.py` |
| `eventhub-machine-telemetry-listen-conn` | Databricks streaming (listen-only SAS policy) |
| `eventhub-machine-telemetry-send-conn` | Demo simulator only (send-only policy) |
| `snowflake-publisher-private-key` | Databricks publisher (PKCS#8 PEM) |
| `powerbi-tenant-id`, `powerbi-sp-client-id`, `powerbi-sp-client-secret` | Databricks `refresh_powerbi` task |

Databricks reads these through an **Azure Key Vault-backed secret scope** named `kv-nf-mfg-<env>`.
`dbutils.secrets.get()` values are redacted in notebook output. ADF Web activities that touch
secrets set `secureInput`/`secureOutput`, so they never appear in the run history.

**Rotation:** passwords and client secrets rotate every 90 days in Key Vault, with no code change.
Snowflake keys rotate using `RSA_PUBLIC_KEY_2`, so there is zero downtime.

## 3. Network
- The self-hosted IR sits inside the plant network and makes **outbound 443 only** to Azure. There are no inbound firewall holes.
- ADLS, Key Vault, the control DB and Event Hub use **private endpoints**; public network access is disabled in prod.
- The Databricks workspace is VNet-injected with **secure cluster connectivity** (no public IPs). Egress goes through a NAT gateway with fixed IPs, which the Snowflake network policy allows.

## 4. Data access (Unity Catalog)
- Catalog per environment. Only the job service principal writes. Engineers get read access in dev and to `control` in prod. Analysts read `gold` only.
- Unity Catalog audit logs capture every table access. Lineage is captured automatically.
- Raw storage is not directly readable by users. It is reached only through external locations granted to the job principal.

## 5. Snowflake
- `MANAGED ACCESS` schemas (grants are controlled centrally, not by object owners).
- Least-privilege roles per consumer. Separate warehouses for loading and BI.
- A resource monitor on BI spend.
- Row access policy `RAP_PLANT` driven by `SECURITY.PLANT_ENTITLEMENT`.

## 6. Power BI
- DirectQuery tables use SSO (Entra ID), so Snowflake enforces row security for the real user.
- The Import model uses the RLS role `Plant Scoped`, fed by the same entitlement table.
- Workspace access goes through Entra groups. The refresh is triggered by a service principal with the workspace *Member* role.

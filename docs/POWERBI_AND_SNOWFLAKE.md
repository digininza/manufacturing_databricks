# Serving: Snowflake + Power BI

## 1. Databricks → Snowflake publish

| Step | Detail |
|---|---|
| Connector | The Spark Snowflake connector, `format("snowflake")`, bundled in Databricks Runtime |
| Auth | **Key-pair**: service user `SVC_DATABRICKS_PUBLISHER` (`TYPE = SERVICE`). The private key lives in Key Vault and is read through the secret scope. A network policy restricts the user to the Databricks NAT IPs. |
| What is sent | Only **changes**: gold's Change Data Feed since the last published version (`control.watermarks[snowflake_publish]`) |
| How it lands | Changes are overwritten into `STAGE.<TABLE>_CHANGES`, then **one MERGE** into `GOLD.<TABLE>` handles upserts and deletes in one Snowflake transaction |
| Order | Dimensions before facts, so Snowflake never holds a fact whose SK is missing |
| Reconciliation | COUNT + SUM(measure) per table, Delta against Snowflake. The watermark advances only if they match. |
| Retry-safety | Stage overwrite + MERGE + watermark-after-reconcile make the task safe to retry (2 automatic retries) |
| Warehouse | `WH_MFG_LOAD` (SMALL, auto-suspend 60s), separate from the BI warehouse |

**Why copy gold into Snowflake at all?** Snowflake is the company's enterprise BI and
data-sharing standard. The Power BI gateways, Finance's SQL analysts and the other business
units' data are all there. Databricks stays the engineering platform and the system of record;
Snowflake is a reconciled serving copy.

## 2. Two consumption paths: views vs semantic model

This is the most common interview question about this project.

### Path A: Power BI semantic model (Import) on the GOLD star schema
- **Tables:** `GOLD.FACT_*` and `GOLD.DIM_*` directly (see `powerbi/`).
- **Used for:** Plant OEE, machine drill-down, quality and defects, maintenance, and supplier pages.
- **Why:**
  - Users slice by any combination of machine, product, shift, date hierarchy and plant, and use time intelligence (YoY, MTD, 7-day rolling OEE).
  - Import (VertiPaq) answers in milliseconds, and DAX handles ratio KPIs correctly at every level.
  - The star schema maps 1:1 to the model.
- **Freshness:** event-driven refresh after each reconciled publish.
- **Volume:** **incremental refresh** keeps 3 years and refreshes the last 10 days, which covers late CDC corrections.

### Path B: Snowflake REPORTING views (DirectQuery)
| View | Consumer | Why a view, why DirectQuery |
|---|---|---|
| `VW_SHOPFLOOR_MACHINE_STATUS` | Shop-floor screens, supervisors | **Freshness.** MES is published hourly. DirectQuery plus a 15-minute page refresh shows it without waiting for a model refresh. One row per machine, so it is cheap. |
| `VW_EXEC_KPI_MONTHLY` | Exec scorecard, **Finance SQL users** | **One definition.** Finance queries the same view in SQL and Excel, so the numbers match by construction. It returns fewer than 100 rows a year. |
| `VW_SCRAP_COST_DAILY` | Finance month-end | Reconciles to the ERP cost ledger. Point-in-time unit cost (SCD2) is encoded in SQL once. |
| `VW_PLANT_OEE_DAILY` | Plant managers' daily e-mail (Snowflake alert / notebook), BI | Component-based OEE roll-up defined once |
| `VW_MAINTENANCE_RELIABILITY_MONTHLY`, `VW_DEFECT_PARETO`, `VW_SUPPLIER_QUALITY_MONTHLY` | Reliability, quality and procurement teams (SQL, Excel, BI) | Pre-aggregated, shared across tools |

### The decision rule
| Question | → Import semantic model | → Snowflake view + DirectQuery |
|---|---|---|
| Heavy, ad-hoc slicing across many dimensions? | ✔ | |
| Time intelligence / complex DAX? | ✔ | |
| Must reflect data within minutes of publish? | | ✔ |
| Must match another tool's number exactly (Finance, Excel)? | | ✔ (logic in SQL) |
| Small result set? | either | ✔ |
| Must enforce row security by the *real user* in the warehouse? | (Power BI RLS) | ✔ (Snowflake row access policy + SSO) |
| Large fact, many visuals per page? | ✔ | ✘ (every visual becomes a Snowflake query, which is slow and costly) |

**Composite model:** `Plant` is **Dual**, so one slicer filters both the Import facts and the
DirectQuery tables without "limited" relationships.

## 3. Security across both paths
- One entitlement table: `SECURITY.PLANT_ENTITLEMENT`.
  - **DirectQuery:** SSO passes the user's Entra identity to Snowflake, and the **row access policy** `RAP_PLANT` filters rows.
  - **Import:** the refresh runs as `SVC_POWERBI_REFRESH` (which the policy lets through). The **Power BI RLS role** `Plant Scoped` applies the same entitlement table with `USERPRINCIPALNAME()`.
- Warehouses: `WH_MFG_BI` is multi-cluster (1–3) for Monday-morning concurrency, with resource monitor `RM_MFG_BI` (notify at 80%, suspend at 100%).

## 4. Refresh orchestration
Databricks job tasks run in this order: `publish_snowflake` → `reconciliation_report` → `refresh_powerbi` (REST API, service principal).
If any reconciliation is open, the refresh is **not** triggered. Users keep seeing yesterday's
consistent numbers, plus the "Data As Of" footer, rather than today's wrong ones.

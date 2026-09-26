# Power BI — NorthForge Manufacturing (PBIP project)

`NorthForge_Manufacturing.pbip` is a **Power BI Project**: the semantic model is
stored as **TMDL** text files (one file per table, measures, relationships, roles),
so it lives in Git, is code-reviewed in PRs and deploys through CI/CD.

```
powerbi/
├── NorthForge_Manufacturing.pbip                  <- open this in Power BI Desktop
├── NorthForge_Manufacturing.SemanticModel/
│   └── definition/
│       ├── model.tmdl            model settings + table list
│       ├── expressions.tmdl      Power Query parameters (Snowflake server/warehouse/db, RangeStart/RangeEnd)
│       ├── relationships.tmdl    star-schema relationships (fact -> dim on surrogate keys)
│       ├── roles/Plant Scoped.tmdl   row-level security
│       └── tables/*.tmdl         columns, M partitions (Snowflake), incremental refresh, measures
├── NorthForge_Manufacturing.Report/               report pages (see REPORT_SPEC.md)
├── REPORT_SPEC.md                                 page-by-page visuals, fields, interactions
└── dax_queries/                                   DAX used to reconcile Power BI with Snowflake
```

## Open it

1. Power BI Desktop (2024+) → *Options → Preview features → Power BI Project (.pbip) save option* and *Store semantic model using TMDL format* ON.
2. Open `NorthForge_Manufacturing.pbip`.
3. *Transform data → Edit parameters*: set `SnowflakeServer` to your account URL. Sign in to Snowflake with your Microsoft Entra ID account, or with the `SVC_POWERBI_REFRESH` key pair.
4. Refresh. The report pages are scaffolded. Build the visuals from `REPORT_SPEC.md`, or use them as a checklist.

## Storage-mode design (the interview question)

| Table | Mode | Source | Why |
|---|---|---|---|
| Fact Production Daily, Fact Machine Daily | **Import + incremental refresh** (3 years kept, last 10 days refreshed) | `GOLD` star schema | Heavy slicing, time intelligence (YoY/MTD/rolling), sub-second visuals. The 10-day refresh window re-reads late MES corrections that arrive via CDC. |
| Fact Quality Inspection / Maintenance / Supplier | Import | `GOLD` | Small to medium, and needed for drill-through. |
| Date, Machine, Product, Defect Type, Supplier | Import | `GOLD` | Small dimensions. SCD2 versions are resolved via surrogate keys. |
| **Plant** | **Dual** | `GOLD` | Filters both the Import facts and the DirectQuery tables without "limited" relationships. |
| **Shopfloor Status** | **DirectQuery** | `REPORTING.VW_SHOPFLOOR_MACHINE_STATUS` | MES is published hourly, so the report must not wait for the next dataset refresh. The result set is tiny (one row per machine), and a 15-minute automatic page refresh is set. |
| **Exec KPI Monthly** | **DirectQuery** | `REPORTING.VW_EXEC_KPI_MONTHLY` | KPI logic lives once in governed SQL, so Finance's Snowflake SQL and Power BI show identical numbers. Under 100 rows a year, so DirectQuery is instant. |

**When is a Snowflake view used, and when a semantic model?**

- **Semantic model (Import on GOLD tables):** exploratory, highly sliced analysis. Used for plant OEE by machine, product, shift or day, defect drill-downs, and trends. DAX handles the time intelligence, and the refresh is scheduled after the Databricks publish.
- **Snowflake views (DirectQuery):**
  - (a) freshness-critical, small results, such as shopfloor status;
  - (b) KPIs that other tools must match exactly, such as exec KPIs and scrap cost;
  - (c) data where Snowflake row access policies must follow the signed-in user through SSO.
- **Never:** DirectQuery on large facts for slice-and-dice. Every visual would become a Snowflake query, which is slow and burns credits.

## Security

- **Import:** the `Plant Scoped` RLS role filters `Plant` and `Machine` by `USERPRINCIPALNAME()` against `Plant Entitlement`. That table is loaded from Snowflake `SECURITY.PLANT_ENTITLEMENT`.
- **DirectQuery:** the dataset is configured for *SSO via Microsoft Entra ID*, so the **Snowflake row access policy** `RAP_PLANT` filters rows for the real user.
- Both paths use **one entitlement table**, so there are no duplicated security rules to drift apart.

## Measures (display folders)

- **Production:** Units Produced/Good/Scrapped, Scrap Rate %, Run Hours, Ideal Units, plus YoY, PY and MTD.
- **Quality:** First Pass Yield %, Defects PPM, Inspection Fail Rate %.
- **OEE:** Availability % × Performance % × Quality % = **OEE %**, re-derived from summed components at every level, never averaged. Also OEE vs Target and OEE 7D Rolling.
- **Maintenance:** Breakdowns, MTTR, MTBF, Open Work Orders.
- **Cost:** Scrap Cost, which uses the unit cost valid on the production day (SCD2).
- **Operational:** Data As Of, shown in every page footer.

## Deployment

The workspaces are Dev → Test → Prod, deployed with Fabric deployment pipelines or with `pbi-tools`/Tabular Editor CLI in CI. Parameter rules swap `SnowflakeDatabase` per stage (`MFG_DW_DEV` / `MFG_DW_TEST` / `MFG_DW`). The scheduled refresh is triggered by the Databricks job's last task (see `resources/jobs/mfg_batch_pipeline.yml`) through the Power BI REST API `POST /datasets/{id}/refreshes`, so Import data refreshes right after a successful, reconciled publish, never before.

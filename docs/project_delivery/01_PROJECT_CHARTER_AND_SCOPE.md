# 01 · Project charter and scope

| Doc ID | NFMFG-DOC-01 | Owner | Programme manager + data architect | Approver | Business sponsor (COO) | Status | Template (pre-filled) |
|---|---|---|---|---|---|---|---|

## 1. Problem statement
Plant KPIs (output, scrap, OEE, downtime) are compiled manually from MES, ERP and maintenance
extracts in Excel. Numbers differ between plants and finance, are 1–2 days late, and IoT sensor
data is not used at all.

## 2. Objectives and success measures
| Objective | Measure | Target |
|---|---|---|
| Single trusted KPI source | Reconciled KPIs published daily, with no manual steps | 100% of the in-scope KPIs |
| Timeliness | Daily KPIs available by | 06:00 UTC; hourly for shop-floor status |
| Trust | Reconciliation breaks reaching users | 0 (pipeline blocks on a break) |
| Machine health | Sensor anomaly minutes per machine per day | Available in Power BI |

## 3. Scope
| In scope | Out of scope (this phase) |
|---|---|
| MES (SQL Server): machines, production runs, inspections, via CDC | MES recipes / process parameters |
| ERP (Oracle): plants, products, production orders | Finance GL, procurement orders |
| CMMS REST API: work orders | Spare-parts inventory |
| Supplier delivery files | Supplier portal integration |
| IoT telemetry for 3 plants (~40 machines) | Edge analytics / ML models (phase 2) |
| Databricks medallion, Snowflake serving, Power BI model + 7 report pages | Real-time alerting to operators (phase 2) |

## 4. Milestones
| Milestone | Gate document(s) |
|---|---|
| M0 Kick-off | Charter (01) |
| M1 Discovery complete | BRD (02), ICDs signed (03), profiling (04) |
| M2 Design approved (ARB) | HLD (05), NFR (08), Security (09) |
| M3 Build complete per epic | LLD (06), STTM (07), DQ spec (10) |
| M4 UAT sign-off | Test strategy / UAT evidence (11) |
| M5 Go-live | Release plan (12), ops handover (13) |
| M6 Hypercare exit (4 weeks) | Support acceptance (13) |

## 5. Constraints and assumptions
Log them in the RAID log (doc 14). Examples:
- **Constraint:** no GoldenGate licence, so Oracle is loaded with a watermark.
- **Assumption:** the MES DBA can enable CDC within the change windows.

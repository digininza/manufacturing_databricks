# 02 · Business requirements document (BRD) + KPI glossary

| Doc ID | NFMFG-DOC-02 | Owner | Business analyst | Contributors | Plant ops, quality, maintenance, finance | Approver | VP Operations |
|---|---|---|---|---|---|---|---|

## 1. Personas and needs
| Persona | Need | Frequency | Delivered by |
|---|---|---|---|
| Plant manager | OEE, output and scrap by line/machine/product; trend vs target | Daily | Power BI page "Plant OEE" (Import) |
| Shift supervisor | Which machines are down or degraded **now** | Every 15 min | "Shopfloor Live" (DirectQuery) |
| Quality engineer | Defect Pareto, FPY, correlation with process conditions | Daily / ad hoc | "Quality & Defects" + `fact_process_run_sensor` |
| Maintenance lead | MTTR, MTBF, open work orders | Daily | "Maintenance" |
| Finance controller | Scrap cost that ties to the ERP cost ledger | Monthly close | `VW_SCRAP_COST_DAILY` / exec KPI view |
| Procurement | Supplier reject rate | Weekly | "Supplier Quality" |

## 2. Functional requirements (examples; each becomes a Jira story linked here)
| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-01 | Daily output per machine and product | Must | Matches MES production screens for 5 sampled machine-days (±0) |
| FR-02 | Corrections in MES are reflected, including deletes | Must | A corrected run shows the corrected value on the next load; a deleted run disappears |
| FR-03 | OEE per machine, line and plant, with the components visible | Must | Formula per the KPI glossary; roll-ups are re-derived, not averaged |
| FR-04 | History is kept when a machine moves line or a product's cost changes | Must | Last month's line-level OEE is unchanged after the move |
| FR-05 | Plant managers see only their plant | Must | Verified with 3 test users in UAT |
| FR-06 | Shop-floor status within 1 hour of MES | Should | Timestamp on the page is ≤ 60 min old during shifts |

## 3. KPI glossary (the single definition, which Power BI, the SQL views and finance must all match)
| KPI | Definition | Formula | Grain / roll-up rule | Owner |
|---|---|---|---|---|
| Units produced | Parts reported complete by MES | Σ `units_produced` | Additive | Plant ops |
| Scrap rate | Share of produced units scrapped | Σ scrapped / Σ produced | Re-derive at every level | Quality |
| First pass yield | Sampled units with no defect | 1 − Σ defect units / Σ sampled units | Re-derive | Quality |
| Availability | Planned time not lost to downtime | Σ (planned − downtime) / Σ planned; planned = 3 shifts × 480 min | Re-derive | Plant ops |
| Performance | Speed vs standard | min(1, Σ produced / Σ ideal), ideal = run min × 60 / std cycle time (as of the production day) | Re-derive | Plant ops |
| Quality (OEE) | Good vs total | Σ good / Σ produced | Re-derive | Quality |
| **OEE** | Overall Equipment Effectiveness | Availability × Performance × Quality | **Never average OEE %** | Plant ops |
| MTTR | Mean time to repair | Σ breakdown repair minutes / # breakdowns | Re-derive | Maintenance |
| MTBF | Mean time between failures | Σ operating hours / # breakdowns | Re-derive | Maintenance |
| Scrap cost | Cost of scrapped units | Σ scrapped × unit cost valid on the production day | Additive | Finance |
| Production day | Business date of a run | Date the **shift started** (the C shift 22:00–06:00 belongs to its start day) | n/a | Plant ops |

Implementation: `docs/DATA_MODEL.md` §3, `powerbi/…/_Measures.tmdl`, `sql/snowflake/03_reporting_views.sql`.

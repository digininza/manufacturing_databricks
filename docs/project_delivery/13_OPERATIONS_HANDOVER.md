# 13 · Operations handover, runbook and knowledge transfer

| Doc ID | NFMFG-DOC-13 | Owner | Data engineering team | Receiving team | L2 application support, L3 data engineering | Approver | Service owner |
|---|---|---|---|---|---|---|---|

## 1. Support model
| Level | Who | Handles | Hours |
|---|---|---|---|
| L1 | Service desk | Ticket intake, user access requests | 24×7 |
| L2 | App support | Monitor alerts, reruns, watermark procedures from the runbook, quarantine follow-up | Business hours + on-call for the 02:00–06:00 batch |
| L3 | Data engineering | Code defects, design changes, performance | Business hours; escalation for S1 |

## 2. Handover pack (what support receives)
| Item | Location |
|---|---|
| Runbook (daily checks, procedures) | [`RUNBOOK.md`](../../RUNBOOK.md) |
| Troubleshooting guide (16 scenarios) | [`docs/TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) |
| Alert catalogue: every alert, its meaning and first action | Logic App payload fields + Azure Monitor rules + Databricks health rules |
| Access for support accounts | Doc 09 §2 |
| Contacts for each source (from ICDs) | Doc 03 |
| KT session recordings | Linked on the Confluence page (not attached; there are size limits) |

## 3. Knowledge transfer plan
| Session | Content | Attendees | Evidence |
|---|---|---|---|
| KT-1 | Architecture and data flow walk-through | L2, L3 | Recording + quiz |
| KT-2 | ADF framework: control tables, rerun, watermark reset | L2 | Shadowed rerun |
| KT-3 | Databricks jobs, reconciliation report, quarantine | L2 | Shadowed incident |
| KT-4 | Snowflake publish, Power BI refresh, RLS requests | L2, BI | Walk-through |
| Reverse KT | Support runs the daily checks while the dev team observes | L2 | Checklist signed |

## 4. Hypercare (4 weeks after go-live)
The dev team is on the first line with support shadowing. Exit criteria: 10 consecutive clean runs, no open S1/S2,
reverse KT complete, and the service owner signs acceptance.

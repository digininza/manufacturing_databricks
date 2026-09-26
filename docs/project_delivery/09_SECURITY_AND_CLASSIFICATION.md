# 09 · Security, access and data classification

| Doc ID | NFMFG-DOC-09 | Owner | Data architect + security engineer | Approver | InfoSec | Confluence | **View-restricted page** (project core team + InfoSec) |
|---|---|---|---|---|---|---|---|

The technical design is in [`docs/SECURITY.md`](../SECURITY.md). This document adds the parts InfoSec signs.

## 1. Data classification
| Data | Classification | Handling |
|---|---|---|
| Production, quality and OEE KPIs | Internal | Standard access groups |
| Unit cost, scrap cost | **Confidential** (commercial) | Finance + management groups; RLS by plant |
| Supplier names, reject rates | Confidential | Procurement + quality |
| Operator / inspector ids | **Personal data (pseudonymous)** | Ids only, no names in the platform; not exposed in Power BI |
| Credentials, keys | **Restricted** | Key Vault only. Never in Git, Confluence, Jira or chat. |

## 2. Access matrix (who gets what)
| Group | Databricks | Snowflake | Power BI |
|---|---|---|---|
| Data engineers | dev: all; prod: read `control`, `gold` | dev only | Dev workspace |
| Support (L2) | prod: read `control` | none | none |
| Analysts | read `gold` | `MFG_ANALYST_ROLE` (REPORTING views) | Viewer |
| Plant managers | none | via DirectQuery SSO (row access policy) | App audience, RLS `Plant Scoped` |
| Service principals | run-as jobs | `SVC_*` key-pair users | Refresh SP |

## 3. Approvals and evidence InfoSec requires
- [ ] Threat model reviewed (data flows in `docs/ARCHITECTURE.md`)
- [ ] Secrets inventory (`docs/SECURITY.md` §2) with owners and a rotation schedule
- [ ] Private endpoints / network diagram (kept in a restricted repository, not on open pages)
- [ ] Access review cadence: quarterly, owned by the service owner
- [ ] Pen test / vulnerability scan before go-live

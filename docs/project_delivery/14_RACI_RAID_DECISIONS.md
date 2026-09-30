# 14 · RACI, RAID log and decision log (ADR)

| Doc ID | NFMFG-DOC-14 | Owner | Programme manager + data architect | Where | RAID items live in **Jira** (issue types Risk / Issue / Dependency); this page embeds them with the Jira macro |
|---|---|---|---|---|---|

## 1. RACI (R = responsible, A = accountable, C = consulted, I = informed)
| Deliverable | Sponsor | Business owner | PM | Data architect | Sr data engineer | Data engineers | BA | QA | Source owners / vendors | InfoSec | Support |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Charter (01) | A | C | R | C | I | I | C | I | I | I | I |
| BRD / KPI glossary (02) | I | A | C | C | C | I | R | C | C | I | I |
| Interface agreements (03) | I | I | C | A | R | C | C | I | **R (hand over)** | C | I |
| Profiling (04) | | I | I | A | R | R | C | I | C | | |
| HLD (05) | I | C | I | A/R | C | I | I | I | I | C | I |
| LLD (06) | | | I | A | R | R | I | C | | | I |
| STTM (07) | | C | I | A | R | R | R | C | C | | |
| NFR (08) / Security (09) | I | C | I | A/R | C | I | | I | | **A (sec)** | C |
| DQ & recon spec (10) | | A | I | C | R | R | R | C | C | | I |
| Test / UAT (11) | | A (UAT) | C | C | C | R | C | R | | C | I |
| Release (12) | | I | C | C | R | R | | C | I | | I |
| Ops handover (13) | | I | C | C | R | R | | | | | **A** |

## 2. RAID log (examples)

> The full, live-style RAID log is in [17_RAID_LOG.md](17_RAID_LOG.md), and the scored risk register with heat maps is in [16_RISK_MATRIX.md](16_RISK_MATRIX.md). The table below is the short summary shown at kick-off.
| ID | Type | Description | Owner | Mitigation / action | Status |
|---|---|---|---|---|---|
| R-01 | Risk | Oracle hard deletes not visible to the watermark load | Architect | ICD: ERP soft-deletes; weekly key compare; GoldenGate in phase 2 | Open |
| R-02 | Risk | SHIR single point of failure | Platform | 2-node HA IR | Closed |
| R-03 | Risk | CDC retention (3 days) shorter than a long outage | Sr DE | Retention raised to 7 days; LSN-gap detection throws | Closed |
| A-01 | Assumption | MES timestamps are UTC | BA | Confirmed in ICD §1 | Validated |
| I-02 | Issue | 0.5% of runs have scrap > produced | MES owner | DQ quarantine; MES UI validation fix requested | In progress |
| D-01 | Dependency | Snowflake network policy needs the Databricks NAT IPs | Platform | Ticket NET-231 | Open |

## 3. Decision log (Architecture Decision Records)
Each decision is a short record: context, options, decision, consequences, date and approver.
Store them in Git (`docs/adr/NNNN-title.md` if desired) and list them here.

| ADR | Decision | Alternatives considered | Rationale | Date / approver |
|---|---|---|---|---|
| ADR-01 | ADF extracts; Databricks transforms | Databricks JDBC to on-prem | SHIR is the approved gateway; native CDC/REST connectors; Purview lineage | ARB |
| ADR-02 | Oracle via `LAST_UPDATE_DATE` watermark | GoldenGate CDC | No licence this phase; delete risk accepted (R-01) | ARB |
| ADR-03 | Deterministic hash surrogate keys | IDENTITY | Rebuildable, environment-independent | Design review |
| ADR-04 | Liquid clustering except the IoT tables, which are date-partitioned | Hive partitioning everywhere | Small-file risk on daily facts | Design review |
| ADR-05 | Snowflake as the serving layer | Databricks SQL | Enterprise BI standard; finance SQL users | ARB |
| ADR-06 | Import model for analytics; DirectQuery views for fresh / shared KPIs | All-DirectQuery | Performance, cost, a single KPI definition | BI design review |

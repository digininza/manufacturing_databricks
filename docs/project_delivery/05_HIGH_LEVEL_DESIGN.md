# 05 · High-level design (HLD)

| Doc ID | NFMFG-DOC-05 | Owner | Data architect | Reviewers | Enterprise architecture, InfoSec, platform team | Approver | Architecture review board |
|---|---|---|---|---|---|---|---|

The HLD answers **what** is built and **why**, at a level that a non-engineer approver can sign.
It does not contain table columns or code; that is the LLD.

## Contents (mandatory sections)
| § | Section | Content for this project | Detail lives in |
|---|---|---|---|
| 1 | Context & goals | Business problem, in/out of scope | Docs 01, 02 |
| 2 | Architecture overview | ADF → ADLS → Databricks medallion → Snowflake → Power BI; Event Hub streaming | `docs/ARCHITECTURE.md` §3 (diagram) |
| 3 | Source integration patterns | CDC (MES), watermark (Oracle), API, files, streaming, with rationale | `docs/CDC_EXPLAINED.md` |
| 4 | Data layers & responsibilities | Raw / bronze / silver / quarantine / gold / control | `docs/ARCHITECTURE.md` §3 |
| 5 | Orchestration | ADF master → Databricks job → publish → Power BI refresh | `docs/ARCHITECTURE.md` §4 |
| 6 | Data model (conceptual) | Star schema, grains, SCD2 dims | `docs/DATA_MODEL.md` |
| 7 | Consumption | Import semantic model vs DirectQuery views, and why | `docs/POWERBI_AND_SNOWFLAKE.md` |
| 8 | Non-functional design | SLA, scale, DR, cost | Doc 08 |
| 9 | Security | Identities, secrets, network, RLS | Doc 09, `docs/SECURITY.md` |
| 10 | Operational controls | Idempotency, reconciliation, DQ, alerting, lineage | `docs/ADF_FRAMEWORK.md`, `docs/DATA_QUALITY_AND_RECONCILIATION.md`, `docs/LINEAGE.md` |
| 11 | Key decisions | ADR summary table | Doc 14 decision log |
| 12 | Environments & deployment | dev / test / prod, CI/CD | `docs/ARCHITECTURE.md` §6, doc 12 |
| 13 | Risks & open points | From RAID | Doc 14 |

## ARB review checklist (what the reviewers will ask)
- [ ] Every source has a signed ICD (03)
- [ ] Every in-scope NFR maps to a design element (08 → §8)
- [ ] No secrets outside Key Vault; managed identities where possible (09)
- [ ] Failure modes: what happens on a partial failure, a rerun or late data? (§10)
- [ ] Cost estimate, and who pays (compute, Snowflake credits, Power BI capacity)
- [ ] Exit strategy / vendor lock-in noted (Delta is open format; Snowflake is a serving copy)

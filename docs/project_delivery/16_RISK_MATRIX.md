# 16 · Risk matrix and risk register

| Doc ID | NFMFG-DOC-16 | Owner | Programme manager (register) · data architect (technical risks) | Reviewed | Weekly RAID review (Tuesday) · monthly at steering committee | Snapshot | **2026-09-30** (Sprint 9 of build) |
|---|---|---|---|---|---|---|---|

The register as a spreadsheet: [raid/RISK_REGISTER.csv](raid/RISK_REGISTER.csv) (opens in Excel). In Jira the same rows are
issues of type **Risk** in project `NFMFG`, and the Confluence page embeds them with the Jira macro.

**Project timeline used below:**

| Phase | Dates |
|---|---|
| Kick-off | 2026-06-01 |
| Discovery | to 2026-06-26 |
| Design | to 2026-07-24 |
| Build (2-week sprints) | 2026-07-27 → 2026-10-30 |
| SIT | November |
| UAT | 2026-11-16 → 12-04 |
| **Go-live** | **2026-12-07** |
| Hypercare | to 2027-01-08 |

---

## 1. What a risk is (and isn't)
| | Risk | Issue |
|---|---|---|
| Definition | Something that **might** happen and would hurt the project | Something that **has** happened and is hurting the project now |
| Example | "The MES DBA *might not* be able to enable CDC in the change window" | "The firewall *is* blocking port 1433; ingestion in dev is stopped" |
| What you do | Reduce the likelihood or the impact **before** it happens | Fix it, or work around it **now** |

When a risk materialises, it is **closed as a risk and raised as an issue**, and the two are linked.
Example: R-04 (Oracle deletes and missed updates) led to issue I-03 (the bulk loader bypasses the trigger).

## 2. Scoring scales

**Likelihood (L)**
| Score | Label | Meaning |
|---|---|---|
| 1 | Rare | < 10% |
| 2 | Unlikely | 10–30% |
| 3 | Possible | 30–50% |
| 4 | Likely | 50–80% |
| 5 | Almost certain | > 80% |

**Impact (I).** Score on the **worst** dimension that applies. These dimensions are specific to data engineering:

| Score | Schedule | Data correctness | Cost | Security / compliance | Operations after go-live |
|---|---|---|---|---|---|
| 1 Negligible | < 1 day | Cosmetic (label, format) | < 1% of budget | None | No user impact |
| 2 Minor | ≤ 1 week, no milestone moves | Few rows wrong, caught by DQ | 1–3% | Internal policy note | Workaround, no SLA miss |
| 3 Moderate | 1–3 weeks, one milestone at risk | A KPI wrong for a subset (one plant or day), corrected by rerun | 3–7% | Audit finding (minor) | Occasional SLA miss |
| 4 Major | > 3 weeks, go-live date at risk | KPIs wrong and **published** to users; trust damaged | 7–15% | Audit finding (major) or access breach contained | Repeated SLA misses |
| 5 Severe | Go-live missed / project stopped | **Silent** data loss; finance numbers wrong at month-end | > 15% | Personal-data breach, regulatory report | Platform unusable |

**Rating = L × I**

| Score | Rating | Action |
|---|---|---|
| 1–4 | 🟩 Low | Accept, review monthly |
| 5–9 | 🟨 Medium | Mitigation owner assigned, reviewed weekly |
| 10–15 | 🟧 High | Active mitigation with a due date, reported in the weekly status report |
| 16–25 | 🟥 Critical | **Escalate to the steering committee within 48 h.** Mitigation funded or scope/date changed. |

## 3. Heat map

### Inherent risk (at identification, before any mitigation)
| Likelihood ↓ / Impact → | 1 Negligible | 2 Minor | 3 Moderate | 4 Major | 5 Severe |
|---|---|---|---|---|---|
| **5 Almost certain** | 🟨 | 🟧 | 🟧 | 🟥 | 🟥 |
| **4 Likely** | 🟩 | 🟨 | 🟧 R-06 R-08 R-10 | 🟥 **R-01 R-05 R-16 R-17** | 🟥 |
| **3 Possible** | 🟩 | 🟨 | 🟨 R-09 R-11 R-14 R-18 R-20 | 🟧 R-04 R-07 R-13 R-15 | 🟧 **R-02 R-03** |
| **2 Unlikely** | 🟩 | 🟩 | 🟨 R-19 | 🟨 | 🟧 R-12 |
| **1 Rare** | 🟩 | 🟩 | 🟩 | 🟩 | 🟨 |

### Current risk (as of 2026-09-30, after mitigations so far)
| Likelihood ↓ / Impact → | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| **5** | 🟨 | 🟧 | 🟧 | 🟥 | 🟥 |
| **4** | 🟩 | 🟨 | 🟧 | 🟥 | 🟥 |
| **3** | 🟩 | 🟨 R-06 R-17 | 🟨 **R-16** | 🟧 **R-13** | 🟧 |
| **2** | 🟩 | 🟩 R-07 R-08 R-09 R-10 R-11 R-14 R-18 R-20 | 🟨 R-04 R-15 R-19 | 🟨 | 🟧 |
| **1** | 🟩 | 🟩 | 🟩 R-12 | 🟩 R-03 | 🟨 |

Closed and not shown: R-01, R-02, R-05.

**Reading the two maps side by side is the story you tell a steering committee:**
- **Discovery-phase risks worked:** 4 critical risks at the start (access, KPI definitions, UAT and parallel-run trust). Access and KPI definitions are closed.
- **R-13 (security approval / pen test) is the one getting worse**, because the pen-test slot is still not confirmed. It is **High** and on this week's steering committee agenda.
- The two go-live risks (UAT availability, parallel-run variance) are reduced, but they will stay open until December by nature.

## 4. Risk register (summary; full columns in the CSV)

| ID | Phase | Category | Risk (cause → event → effect) | L×I inherent | Response | Key mitigation | L×I now | Trend | Owner | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| R-01 | Pre-project | Access / infra | On-prem network changes need CAB → SHIR firewall and private endpoints late → no source connectivity for build | 4×4 = 16 🟥 | Mitigate | Network requests raised in week 1; build on synthetic data in parallel | — | ✔ | Platform lead | **Closed** 2026-07-20 (became I-01, resolved) |
| R-02 | Pre-project | Source system | SQL Agent / CDC can't be enabled in MES prod within change windows → no CDC → no deletes, full loads | 3×5 = 15 🟧 | Mitigate | CDC in the ICD; proven in MES QA; fallback = watermark on `modified_at` | — | ✔ | Sr data engineer | **Closed**. CDC proven in QA; prod enablement is scheduled (D-03). |
| R-03 | Pre-project | Data loss | Pipeline outage longer than CDC retention (3 days) → LSN gap → updates **silently** lost | 3×5 = 15 🟧 | Mitigate | Retention to 7 days; LSN-gap check **throws**; SHIR-offline alert | 1×4 = 4 🟩 | ↓ | Sr data engineer | Monitoring |
| R-04 | Pre-project | Data correctness | Oracle loaded by watermark → hard deletes / untriggered updates invisible → gold differs from ERP | 3×4 = 12 🟧 | Mitigate + accept residual | ICD: soft deletes only; weekly key compare | 2×3 = 6 🟨 | ↓ | Data architect | Open. Partly materialised as **I-03** |
| R-05 | Pre-project | Requirements | Plants and finance disagree on OEE / production-day definitions → rework, no sign-off | 4×4 = 16 🟥 | Avoid | KPI glossary signed by VP Ops before build (DEC-03) | — | ✔ | Business analyst | **Closed** 2026-07-17 |
| R-06 | Discovery | Data quality | Source data worse than assumed → quarantine volumes high → KPIs incomplete | 4×3 = 12 🟧 | Mitigate | Profiling in discovery; quarantine design; named DQ stewards | 3×2 = 6 🟨 | → | Data steward (quality) | Monitoring (0.5% quarantined, I-02) |
| R-07 | Discovery | People | Only one engineer knows the ADF framework and the MES schema → single point of failure | 3×4 = 12 🟧 | Mitigate | Pairing rota, LLDs, recorded walkthroughs | 2×2 = 4 🟩 | ↓ | Tech lead | Open (materialised briefly as I-07) |
| R-08 | Discovery | Scope | Requests for recipes/process parameters and ML → scope creep → build overrun | 4×3 = 12 🟧 | Mitigate | CR process (doc 12); phase-2 backlog | 2×2 = 4 🟩 | ↓ | Programme manager | Monitoring (2 CRs deferred to phase 2) |
| R-09 | Build | Cost | DirectQuery overuse → Snowflake credit overrun | 3×3 = 9 🟨 | Mitigate | DirectQuery only on small views; resource monitor; Import model | 2×2 = 4 🟩 | ↓ | BI lead | Monitoring |
| R-10 | Build | Performance | Skewed sensor × run join in the 1-year backfill → backfill exceeds its window | 4×3 = 12 🟧 | Mitigate | Salting, date partitions, separate backfill cluster | 2×2 = 4 🟩 | ↓ | Sr data engineer | Monitoring. Perf test PT-01 on 2026-10-14. |
| R-11 | Build | Vendor | CMMS API rate limits / unannounced changes → extraction failures | 3×3 = 9 🟨 | Mitigate | Retries + throttling; contract test against the sandbox; notice period in ICD | 2×2 = 4 🟩 | → | Data engineer | Open (I-04 workaround in place) |
| R-12 | Build | Compliance | Operator names in MES free-text fields → personal data in the lake | 2×5 = 10 🟧 | Avoid | Columns excluded; pseudonymous ids only; InfoSec review | 1×3 = 3 🟩 | ✔ | Data architect | **Closed** 2026-08-12 |
| R-13 | Build → Go-live | Security | Pen test / InfoSec sign-off late → go-live blocked | 3×4 = 12 🟧 | Mitigate | Security design (doc 09) approved early; pen-test slot requested 2026-08-25 | **3×4 = 12 🟧** | **↑** | Data architect + InfoSec | **Open, escalated.** Slot still unconfirmed (D-05). |
| R-14 | Build | Dependency | Platform team late with Databricks NAT IPs → Snowflake publish blocked in test | 3×3 = 9 🟨 | Mitigate | Ticket NET-231 escalated; publish tested in dev meanwhile | 2×2 = 4 🟩 | → | Platform lead | Open. Materialised as I-05, tracked by D-02. |
| R-15 | Build | Test | Test environment not production-like in volume → performance problems found in UAT | 3×4 = 12 🟧 | Mitigate | Synthetic generator at 1-year scale; masked prod copy requested | 2×3 = 6 🟨 | ↓ | QA lead | Open |
| R-16 | Go-live | People | Business testers unavailable in Nov (quarter-end) → UAT slips → go-live slips | 4×4 = 16 🟥 | Mitigate | UAT booked outside month-end week; 6 named testers (A-04) | **3×3 = 9 🟨** | ↓ | Programme manager | Open. Names due 2026-10-15. |
| R-17 | Go-live | Adoption | New KPIs differ from the legacy Excel → users distrust the platform | 4×4 = 16 🟥 | Mitigate | 2-week parallel run; variance explanation log; definitions published | 3×2 = 6 🟨 | ↓ | Business analyst | Open |
| R-18 | Go-live | Operations | L2 support not ready at hypercare exit → dev team stuck supporting | 3×3 = 9 🟨 | Mitigate | KT plan + reverse KT (doc 13) | 2×2 = 4 🟩 | → | Service owner | Open |
| R-19 | Run | Technology | Databricks runtime / library deprecation during the project | 2×3 = 6 🟨 | Accept | LTS runtime 15.4; Dependabot; quarterly review | 2×3 = 6 🟨 | → | Tech lead | Accepted |
| R-20 | Run | Capacity | Power BI capacity contention at refresh time → slow reports | 3×3 = 9 🟨 | Mitigate | Event-driven refresh after publish; incremental refresh; Capacity Metrics app | 2×2 = 4 🟩 | → | BI lead | Open |

**Response types:**
- **Avoid:** change the plan so the risk can't happen.
- **Mitigate:** reduce the likelihood or the impact.
- **Transfer:** make someone else carry it (contract, vendor SLA, insurance).
- **Accept:** consciously live with it and monitor.

## 5. How to write a good risk
Use **cause → event → effect**, so the mitigation is obvious:
> ❌ "Performance risk."
> ✅ "*Because* the sensor × production-run join has only ~40 machine keys and presses emit 10× more readings (**cause**), the 1-year backfill *may* run on a few straggler tasks (**event**), so the backfill *could* exceed its weekend window and delay the go-live data load (**effect**)."

Every risk needs:
- an **owner** (a person, not a team);
- a **trigger / early-warning indicator** (for R-10: backfill stage max-task time > 10× the median in the perf test);
- a **review date**.

## 6. Interview talking points
- "We scored risks as likelihood × impact on a 5×5 matrix. Impact was defined in **data** terms: silent data loss or wrong finance numbers score 5, even when the schedule impact is small."
- "The biggest *pre-project* risks were access (on-prem firewall and CAB lead times) and **KPI definitions**. We closed the definition risk by getting the KPI glossary signed *before* build. That one decision removed most of the late rework."
- "Technical risks such as the CDC retention gap turned into design features: gap detection that fails loudly, and a 7-day retention. That's how a risk register feeds architecture."
- "A risk that materialises becomes an issue, linked back. For example, the Oracle watermark risk showed up as a bulk loader that bypassed the trigger. We caught it in a SIT dry run through reconciliation, not in production."

# 15 · Team working agreement: how every engineer uses these documents

| Doc ID | NFMFG-DOC-15 | Owner | Tech lead | Agreed by | Whole team (Sprint 0) |
|---|---|---|---|---|---|

## 1. Traceability chain (nothing is built that isn't traceable)
`BRD requirement (FR-xx) → HLD section → LLD page → STTM mapping_id → Jira story → PR → test case → UAT sign-off`

## 2. Jira conventions
| Item | Rule |
|---|---|
| Epic | One per LLD (Ingestion, Silver, Gold, Streaming, Publish, Power BI); links to the LLD page |
| Story | One per target table or pipeline change. Description lists the `mapping_id`s and the DQ rule ids. |
| Branch / PR | `feature/NFMFG-123-silver-production-log`. The PR title starts with the Jira key, so it shows up in the Jira development panel. |
| Sub-tasks | Build · unit test · update docs · SIT |
| Labels | `source:mes`, `layer:silver`, `doc-change` |

## 3. Definition of Ready (a story can't enter a sprint without these)
- [ ] The source ICD (03) is signed for the objects involved
- [ ] The STTM rows exist and are **Approved** (07)
- [ ] DQ rules and severities are agreed (10)
- [ ] Acceptance criteria and test case ids are written (11)
- [ ] Dependencies (access, network, credentials in Key Vault) are resolved or planned

## 4. Definition of Done
- [ ] Code merged via PR with 1+ reviewer; CI green (black, flake8, pytest, bundle validate)
- [ ] Implements the STTM exactly; any deviation means the STTM is updated in the **same PR**
- [ ] Metadata, not code, for new sources (control row + registry block)
- [ ] Reconciliation checks pass in dev and test
- [ ] Runbook / troubleshooting updated for new failure modes
- [ ] Confluence page updated (or auto-published) and the Jira story linked to it
- [ ] Demo'd in the sprint review

## 5. Review rules
| Change | Required reviewers |
|---|---|
| STTM / DQ rules | BA + data steward (+ source owner for the source side) |
| Control table / ADF | Another senior DE |
| Gold model / KPI | Architect + BI lead |
| Security-relevant (identities, secrets, grants, RLS) | Architect + InfoSec notification |

## 6. Documentation hygiene
- Write for the next engineer: every LLD answers "what happens on failure, and how do I rerun?"
- No secrets, personal data or production extracts in Git, Confluence or Jira (doc 00 §3.2)
- Document owners review their pages every quarter, and after every release that touches them

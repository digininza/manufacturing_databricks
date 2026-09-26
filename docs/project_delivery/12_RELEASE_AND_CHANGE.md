# 12 · Release, deployment and change management

| Doc ID | NFMFG-DOC-12 | Owner | DevOps / platform engineer | Approver | Change Advisory Board (prod releases) |
|---|---|---|---|---|---|

## 1. Environments and promotion
`feature branch → PR (CI: black, flake8, pytest, bundle validate) → main → dev → test (SIT/UAT) → prod`.

| Component | Mechanism |
|---|---|
| Databricks | Asset Bundles: `databricks bundle deploy -t <env>` from CI, run as a service principal |
| ADF | Git mode; ARM template built by `@microsoft/azure-data-factory-utilities`; triggers stopped/started around the deploy |
| Control DB / Snowflake DDL | Versioned migration scripts (e.g. Flyway / schemachange), applied by the pipeline |
| Power BI | Fabric deployment pipeline (Dev → Test → Prod) with parameter rules per stage |

## 2. Release checklist (attach to the CAB ticket)
- [ ] Release notes: Jira fix version, changed documents (STTM/LLD versions)
- [ ] Backward compatibility: schema changes additive? Snowflake / Power BI impact assessed?
- [ ] Control-table changes scripted, not manual
- [ ] Watermark or checkpoint resets required? Documented with a reason (audit)
- [ ] Rollback plan: redeploy the previous bundle / ARM version; Delta `RESTORE TABLE … VERSION AS OF`
- [ ] Post-deploy smoke test: run one entity end to end; reconciliation report green

## 3. Change requests (after baseline)
| Trigger | Process |
|---|---|
| Source schema change (ICD) | The source owner raises a CR at least 10 working days ahead. We assess the impact on STTM/LLD, update the documents (vX.Y+1), build and test, then release in a coordinated window. |
| New KPI / requirement | BA raises a CR, which goes through BRD, STTM and LLD updates, sizing, and prioritisation by the product owner |
| Defect in an approved document | Minor version bump; approvers notified (page watch) |

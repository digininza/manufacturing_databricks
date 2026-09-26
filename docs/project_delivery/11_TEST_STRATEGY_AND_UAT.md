# 11 · Test strategy, test cases and UAT

| Doc ID | NFMFG-DOC-11 | Owner | QA lead + data engineers | Approver | Architect (strategy), business owner (UAT sign-off) |
|---|---|---|---|---|---|

## 1. Test levels
| Level | What | Where / tool | Entry → exit |
|---|---|---|---|
| Unit | Pure logic: SQL builders, recon rules, registry contract, ADF and Power BI artifact integrity | `pytest` in CI (`tests/`) | Every PR; 100% pass |
| Component (Spark) | Silver standardize/DQ/dedup, salted join | `tests/test_spark_*.py` in CI (JDK) | Every PR |
| Integration (SIT) | End-to-end in `test`: ADF → Databricks → Snowflake → Power BI with synthetic day-1/day-2 data | `mfg_setup_and_demo_data` + batch job | Build complete → all SIT cases pass |
| Reconciliation | All 6 checkpoints PASSED, plus manual source spot checks | `reconciliation_report` notebook | Zero open breaks |
| Failure / resilience | Kill a copy mid-run, break a manifest, LSN gap, duplicate file, schema change, rerun twice | Demo notebooks + ADF manual runs | Each recovers with no loss or duplicates |
| Performance | 1-year backfill, peak IoT load, Power BI page load < 3 s | Test environment at production-like scale | NFR-01/03 met |
| Security | RLS per persona; no secrets in logs; access review | Test users per plant | InfoSec sign-off |
| UAT | Business validates KPIs against their known numbers | Power BI Test workspace | Signed UAT form |

## 2. Test case format (traceable to the STTM)
| Test id | Mapping / requirement | Steps | Expected | Result | Evidence |
|---|---|---|---|---|---|
| SIT-07 | STTM-PL-15/16, FR-02 | Land day 2 with CDC update ×2 and delete for runs 100001–100003 | 100001 = 330/12; 100002/3 soft-deleted; gold excludes them | Pass | Jira/Xray execution link |
| SIT-11 | STTM-DM-03, FR-04 | Machine M-P01-03 moves line on day 2 | 2 versions; day-1 facts keep L2 | Pass | Screenshot of demo_02 |
| FT-03 | NFR-04 | Manifest overstated by 5 rows | Bronze task fails, silver not run, alert sent; after the fix, PASSED | Pass | `control.reconciliation_results` |
| UAT-04 | FR-03, KPI glossary | Plant manager compares last week's OEE with the MES OEE screen | Within the agreed definition differences | | Signed by user |

## 3. Defect management
Defects are raised in Jira and linked to the STTM row or requirement they violate. Severity S1–S4.
Go-live criteria: no S1/S2 open, and S3 items have an agreed workaround.

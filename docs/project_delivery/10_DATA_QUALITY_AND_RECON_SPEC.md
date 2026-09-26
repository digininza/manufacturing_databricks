# 10 · Data quality and reconciliation specification

| Doc ID | NFMFG-DOC-10 | Owner | Data engineer + BA | Validated by | Data stewards (quality, finance) | Approver | Business data owner |
|---|---|---|---|---|---|---|---|

The implementation is in [`docs/DATA_QUALITY_AND_RECONCILIATION.md`](../DATA_QUALITY_AND_RECONCILIATION.md), and the rules live in
`configs/source_registry.yml`. This specification is what the **business signs**: which rules exist, and what happens when they fail.

## Rule catalogue (excerpt; the full list is generated from the registry)
| Rule id | Entity | Rule | Severity | Business rationale | Steward | On failure |
|---|---|---|---|---|---|---|
| DQ-PL-01 | production_log | units_produced ≥ 0 and units_scrapped ≥ 0 | ERROR | Negative output is impossible | Plant ops | Quarantine + alert; MES team corrects at source |
| DQ-PL-02 | production_log | units_scrapped ≤ units_produced | ERROR | Scrap is a subset of output | Quality | Quarantine |
| DQ-PL-03 | production_log | shift_code in (A, B, C) | WARN | Shift reports | Plant ops | Row kept as UNKNOWN, counted |
| DQ-QI-01 | quality_inspection | 0 ≤ defect_count ≤ sample_size | ERROR | | Quality | Quarantine |
| DQ-SD-01 | supplier_delivery | 0 ≤ rejected ≤ delivered | ERROR | | Procurement | Quarantine; supplier informed |

## Reconciliation sign-off table
| Checkpoint | Measure | Tolerance | Owner of a break |
|---|---|---|---|
| Source → ADF | Row count | 0 (files: ≤ 2% rejected rows) | Data engineering + source owner |
| ADF → Bronze | Row count per run | 0 | Data engineering |
| Bronze → Silver | in = valid + quarantined + duplicates | 0 | Data engineering |
| Silver → Gold | Count + SUM(units_produced) | 0 | Data engineering |
| Gold → Snowflake | Count + SUM(measure) | Count 0; SUM 0.01% | Data engineering |
| Power BI → Snowflake | DAX vs SQL totals | 0 | BI team (at UAT and after model changes) |

**Quarantine SLA:** the steward reviews daily; the source owner fixes within 2 business days. Rows older than 30 days are escalated.

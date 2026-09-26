# 07 · Source-to-target mapping (STTM)

| Doc ID | NFMFG-DOC-07-<target table> | Owner | Data engineer + BA | Validated by | Source owner (left side), BI lead / data steward (right side) | Approver | Architect + business data owner |
|---|---|---|---|---|---|---|---|

The STTM is **the build contract**. A developer implements exactly what the approved STTM says, and a
tester tests exactly that. If the code needs to differ, the STTM changes first (via PR). One STTM
per **target table**, kept as CSV in [`sttm/`](sttm/) (diffable in Git, opens in Excel) and rendered
as a table on the Confluence page.

## STTM columns (standard for every mapping)
| Column | Meaning |
|---|---|
| `mapping_id` | Unique id, referenced by Jira stories and test cases (e.g. `STTM-PL-07`) |
| `source_system`, `source_object`, `source_column`, `source_type` | Left side, exactly as in the ICD |
| `target_layer`, `target_table`, `target_column`, `target_type` | Right side |
| `transformation_rule` | Plain-English + pseudo-SQL rule |
| `null_handling` | Default or reject |
| `dq_rule` / `severity` | Rule id from doc 10 |
| `key_type` | PK / BK / FK / SK / measure / attribute / audit |
| `scd_type` | 0 / 1 / 2 (dimensions) |
| `comments` | Edge cases, open questions |
| `status` | Draft / Approved / Changed-in-vX |

## Mapping files
| File | Target | Covers |
|---|---|---|
| [sttm/STTM_silver_mes_production_log.csv](sttm/STTM_silver_mes_production_log.csv) | `silver.mes_production_log` | MES CDC → silver: cleansing, CDC columns, audit columns |
| [sttm/STTM_gold_dim_machine.csv](sttm/STTM_gold_dim_machine.csv) | `gold.dim_machine` | SCD2 attributes, surrogate key, effective dating |
| [sttm/STTM_gold_fact_production_daily.csv](sttm/STTM_gold_fact_production_daily.csv) | `gold.fact_production_daily` | Aggregations, SK lookups, derived measures |

**Consistency check.** The silver part of an STTM must match `configs/source_registry.yml`. Reviewers check
both in the same PR. A future CI test can parse the CSV and compare them automatically.

## How the STTM is used by each role
| Role | Uses the STTM to |
|---|---|
| Developer | Implement transformations; each story references `mapping_id`s |
| Tester | Derive test cases one-to-one from rows (doc 11) |
| BI developer | Know the exact meaning and grain of every gold column |
| Support (L2/L3) | Answer "where does this number come from?" |
| Auditor | Column-level lineage evidence (together with Unity Catalog lineage) |

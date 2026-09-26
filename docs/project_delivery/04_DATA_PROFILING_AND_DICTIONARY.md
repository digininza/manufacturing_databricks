# 04 · Data profiling report and data dictionary

| Doc ID | NFMFG-DOC-04 | Owner | Data engineers | Validated by | BA + source owner | Approver | Data architect |
|---|---|---|---|---|---|---|---|

**Why this document exists.** The ICD says what a source *should* contain; profiling shows what it
*does* contain. Every surprise found here becomes a DQ rule (doc 10), an STTM rule (doc 07) or a RAID item.

## 1. Profiling method
- Run on masked samples or a read-only replica. **Only aggregates are published** (no row-level data in Confluence).
- For each column, capture: data type, null %, distinct count, min/max, top values, pattern (regex), and a length histogram.
- For each table, capture: row count, key uniqueness, orphan checks against parents, and the distribution by date.
- Tooling: a Databricks notebook (`df.summary()`, `approx_count_distinct`) or Databricks *Data Profile* in the notebook UI.

## 2. Findings (from the synthetic data; the format is what matters)
| # | Table.column | Finding | Impact | Resolution (where) |
|---|---|---|---|---|
| P-01 | production_log.machine_id | 2.8% of values have leading/trailing spaces or lower case | Joins to dim_machine fail | STTM: `trim, upper` (07) |
| P-02 | production_log.shift_code | 2.7% NULL | Shift reports lose rows | Default `UNKNOWN` + WARN rule |
| P-03 | production_log.units_scrapped | 0.5% of rows have scrap > produced | Negative good units | ERROR rule → quarantine; raised with the MES team (RAID I-02) |
| P-04 | production_log | Same change delivered twice after an extraction retry | Double counting | Exact + latest-per-key dedup |
| P-05 | PRODUCTS.PRODUCT_NAME | 1 NULL | Blank labels in reports | WARN; ERP team to fix |
| P-06 | supplier files | Yesterday's lines re-sent in today's file | Double deliveries | Dedup on `delivery_id` |
| P-07 | IoT | ~0.1% duplicate events; ~0.05% arrive > 10 min late | Inflated counts; missed windows | `dropDuplicatesWithinWatermark`; late events kept in bronze |

## 3. Data dictionary
The column-level dictionary is **generated, not hand-written**:
- source side: the ICD DDL;
- silver: `configs/source_registry.yml` (names, types, transforms);
- gold: table and column `COMMENT`s in `sql/databricks/02_gold_tables.sql`, visible in Unity Catalog Explorer;
- business terms: the KPI glossary (doc 02).

Publish the Unity Catalog view as the living dictionary; link it from Confluence.

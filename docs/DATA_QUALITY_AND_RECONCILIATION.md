# Data quality and reconciliation

## 1. Silver transformations (in order)

| Step | Implementation | Example from the synthetic data |
|---|---|---|
| **Standardization** | `transforms: [trim, upper, …]` per column | `' m-p01-04 '` becomes `'M-P01-04'`; `'acme metals ltd'` becomes `'Acme Metals Ltd'` |
| **Type casting** | `type:` per column (a failed cast becomes NULL, which DQ catches) | Oracle `UNIT_COST` string becomes `decimal(12,2)` |
| **Renaming to the conformed model** | `name:` | `PRODUCT_FAMILY` → `product_family`, `modified_at` → `source_modified_at` |
| **Null handling** | `null_defaults:` | `shift_code NULL → 'UNKNOWN'`, `units_scrapped NULL → 0` |
| **DQ rules** | `dq_rules:` (ERROR → quarantine, WARN → flag) | `units_produced = -12` is quarantined |
| **Deduplication** | Exact duplicates, then latest per key by `_sequence` | ADF landed a change twice; a supplier re-sent a line |
| **CDC apply** | Sequence-guarded MERGE with soft delete | See [CDC_EXPLAINED.md](CDC_EXPLAINED.md) |

## 2. DQ rule engine (`src/framework/dq.py`)

- Rules are SQL predicates that must be TRUE. **NULL counts as failed.**
- There is a single pass per batch:
  - `_dq_errors` and `_dq_warnings` arrays are added to every row;
  - per-rule failure counts are aggregated into `control.dq_results`.
- **ERROR** rows go to `quarantine.<entity>`, together with the list of failed rules and the batch id. A WARNING alert is sent.
- **WARN** rows continue to silver, and `_dq_warnings` stays queryable.
- Quarantined rows are **not** manually patched. The source fixes them, CDC brings the corrected row, and it passes. See row 100009 in the sample data.

## 3. Reconciliation checkpoints

| # | Check | Where | Formula | On failure |
|---|---|---|---|---|
| 0 | **Source → ADF** | `ctl.usp_validate_ingestion_counts` | source_count = rows_read = rows_copied + rows_skipped; skipped ≤ tolerance | Run → RECON_FAILED, THROW, alert. Watermark not advanced. |
| 1 | **ADF → Bronze** | `src/bronze/manifest_recon.py` | manifest.rows_copied = bronze rows for that `_ingestion_run_id` | Bronze task fails, so silver, gold and publish do not run. Re-checked every run until PASSED. |
| 2 | **Bronze → Silver** | `processor.py`, per micro-batch | rows_in = valid + quarantined + duplicates | Batch raises, the checkpoint does not advance, and the batch is replayed |
| 2b | **Silver target verify** | `processor.py` | Every live key of the batch is in silver at ≥ its sequence | Same as above |
| 3 | **Silver → Gold** | `facts.py` | For the affected dates: count(runs) and SUM(units_produced), silver = fact | Raises before the CDF watermark moves |
| 4 | **Gold → Snowflake** | `snowflake_publisher.py` | COUNT and SUM(measure) per table, Delta = Snowflake | That table's publish watermark does not advance, and the task fails |
| 5 | **Power BI → Snowflake** | `powerbi/dax_queries/reconcile_with_snowflake.dax` | DAX totals = Snowflake SQL | Run after model changes (UAT sign-off) |

`notebooks/05_reconciliation/reconciliation_report.py` shows the latest result of every check
and **fails the job if any break is open**. That blocks the Power BI refresh.

## 4. Why "count + sum" and not just count

A count can match while values are wrong: a truncated decimal, a join fan-out that
duplicated one row and dropped another, or a timezone shift that moved rows to another day.
A control total on the key measure (`units_produced`, `delivered_qty`, `downtime_minutes`) catches
these. Snowflake uses a relative tolerance of 0.01% for floating-point sums; counts must match exactly.

## 5. Useful queries

```sql
-- open breaks
SELECT * FROM mfg_prod.control.vw_failed_reconciliations ORDER BY checked_ts DESC;

-- DQ trend: which rules are failing more this week?
SELECT entity_name, rule_name, date(checked_ts) d, sum(failed_count) failed, sum(total_count) total
FROM mfg_prod.control.dq_results GROUP BY ALL ORDER BY d DESC, failed DESC;

-- quarantined rows for a run
SELECT production_log_id, _dq_errors, _ingestion_run_id FROM mfg_prod.quarantine.mes_production_log
WHERE _ingestion_run_id = 'mes_production_log_20260924_01';
```

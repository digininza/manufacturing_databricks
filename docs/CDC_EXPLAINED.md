# CDC and incremental loading, explained with this project's data

## 1. The problem CDC solves

`dbo.production_log` in the MES has millions of rows. Every hour we need to know
**what changed**:

- the new production runs;
- the runs an operator corrected;
- the runs a supervisor deleted because they were entered twice.

There are three ways to find out:

| Approach | How | Sees deletes? | Cost on source | Used here for |
|---|---|---|---|---|
| **Full load** | Copy the whole table every time | Yes (by absence) | High | `erp_plant` (3 rows) |
| **Query-based incremental ("watermark")** | `WHERE last_update_date > :last_run` | **No** | Medium (needs an index) | Oracle ERP, REST API, files |
| **Log-based CDC** | Read the database transaction log | **Yes** | Low | SQL Server MES |

**Log-based CDC** is the best option: the database itself records every INSERT,
UPDATE and DELETE, in commit order, with no reliance on an application
maintaining a timestamp column correctly.

## 2. How SQL Server CDC works

```mermaid
flowchart LR
  APP[MES application] -->|INSERT / UPDATE / DELETE| T[(dbo.production_log)]
  T --> LOG[(transaction log)]
  LOG -->|SQL Agent capture job<br/>reads the log| CT[(cdc.dbo_production_log_CT<br/>change table)]
  CT -->|cdc.fn_cdc_get_all_changes_...<br/>(from_lsn, to_lsn)| ADF[ADF Copy activity]
  CLEAN[SQL Agent cleanup job] -.->|deletes changes older<br/>than retention = 7 days| CT
```

Enabling it is two stored-procedure calls, run by the DBA (see `sql/source_systems/sqlserver_mes_setup_and_cdc.sql`):

```sql
EXEC sys.sp_cdc_enable_db;
EXEC sys.sp_cdc_enable_table @source_schema = N'dbo', @source_name = N'production_log', @role_name = N'cdc_reader';
```

From then on, every change lands in a **change table** with these extra columns:

| Column | Meaning |
|---|---|
| `__$start_lsn` | **Log Sequence Number** of the transaction's commit: a 10-byte, ever-increasing position in the log. **This is our watermark.** |
| `__$seqval` | Order of the change *within* that transaction |
| `__$operation` | **1 = DELETE, 2 = INSERT, 3 = UPDATE (before image), 4 = UPDATE (after image)** |
| `__$update_mask` | Bitmask of which columns the UPDATE touched |

We read the changes through a table-valued function, always for an LSN **window**:

```sql
SELECT * FROM cdc.fn_cdc_get_all_changes_dbo_production_log(@from_lsn, @to_lsn, N'all');
```

`N'all'` returns ops 1, 2 and 4. `N'all update old'` also returns op 3, the "before" row of each update.

## 3. What it looks like: real rows from `sample_data/`

Day 2 (`sample_data/raw/sqlserver_mes/production_log/load_date=2026-09-25/...`) contains
36 inserts, 3 updates and 2 deletes. Here are the interesting ones (a subset of the columns is shown):

**Updates (op 4, after image).** Run 100001 was corrected **twice** in the same hour:

| cdc_start_lsn | cdc_seqval | op | commit ts | production_log_id | units_produced | units_scrapped |
|---|---|---|---|---|---|---|
| 0x0000002A000001F84EFA | 0x…0384 | 4 | 09:00 | 100001 | 330 | 9 |
| 0x0000002A000001F84F43 | 0x…0385 | 4 | 09:05 | 100001 | 330 | 12 |
| 0x0000002A000001F850D5 | 0x…0386 | 4 | 09:00 | 100009 | 410 | 4 |

**Deletes (op 1).** The row carries the values that were deleted:

| cdc_start_lsn | op | production_log_id | units_produced |
|---|---|---|---|
| 0x0000002A000001F85221 | 1 | 100002 | 429 |
| 0x0000002A000001F852A5 | 1 | 100003 | 390 |

**The same updates with `'all update old'`.** Each update produces a before/after *pair*
(`sample_data/cdc_explained/production_log_all_update_old_before_after_pairs.csv`):

| cdc_start_lsn | op | production_log_id | units_produced | units_scrapped |
|---|---|---|---|---|
| …84EFA | **3** (before) | 100001 | 305 | 9 |
| …84EFA | **4** (after) | 100001 | 330 | 9 |
| …84F43 | **3** (before) | 100001 | 330 | 9 |
| …84F43 | **4** (after) | 100001 | 330 | 12 |
| …850D5 | **3** (before) | 100009 | 410 | **415** ← the bad value (scrap > produced) |
| …850D5 | **4** (after) | 100009 | 410 | **4** ← fixed at source |

Row 100009 was **quarantined** on day 1 by the DQ rule `scrap_not_above_produced`.
On day 2 the source fixed it, the op-4 row passed DQ, and silver now holds the correct value.
This is quarantine and CDC working together.

We extract with `'all'` (no op 3) because silver only needs the final state. Op 3 is
useful for auditing "what exactly changed", and that history is already kept in bronze.

## 4. How ADF extracts it (`PL_10_Ingest_SqlServer_CDC`)

1. `Get_High_Watermark`: `SELECT CONVERT(VARCHAR(22), sys.fn_cdc_get_max_lsn(), 1) AS HWM` gives the candidate window end.
2. `Start_Run` (`ctl.usp_start_ingestion_run`) **freezes** the window as (last successful LSN, HWM] and renders the query.
   - A retry of a failed run **reuses the same window**, so the result is identical and nothing is lost or doubled.
3. The rendered query (template in `sql/control_db/03_seed_ingestion_control.sql`):
   - starts at `sys.fn_cdc_increment_lsn(last_lsn)`, because the window is exclusive of what was already loaded;
   - **fails loudly on an LSN gap**: if `from_lsn < sys.fn_cdc_get_min_lsn()`, the cleanup job has already deleted changes we never read. Continuing would silently lose updates. The fix is a full reload plus `ctl.usp_reset_watermark`;
   - converts binary LSNs to fixed-width hex strings (`0x` + 20 hex digits), so they sort correctly as text in Spark.
4. Copy the rows to staging, validate counts, publish to raw, write the manifest, and only then run `usp_complete_ingestion_run`, which sets `last_watermark_value = window_end`.

**First load.** CDC only contains changes made *after* it was enabled. The first run for a
table that already has data is a one-off full snapshot (loaded as op 2 rows); CDC
continues from the LSN captured at that moment.

## 5. How Databricks applies it (silver)

`src/silver/cdc.py`, for every micro-batch:

1. **Order.** `_sequence = cdc_start_lsn | cdc_seqval`.
2. **Dedup.**
   - Exact duplicates are dropped. The same change can land twice if ADF re-published a window.
   - Then only the **latest** version per key is kept. Row 100001's 09:00 version is collapsed and its 09:05 version wins.
3. **MERGE.**

```sql
MERGE INTO silver.mes_production_log t USING batch s ON t.production_log_id <=> s.production_log_id
WHEN MATCHED AND s._sequence > t._sequence THEN UPDATE SET ...   -- only NEWER versions overwrite
WHEN NOT MATCHED AND s._is_deleted = false THEN INSERT ...       -- never insert a delete
```

- `_is_deleted = (cdc_operation = 1)` gives a **soft delete**: the row stays for audit, and gold excludes it.
- `s._sequence > t._sequence` makes it **replay-safe and out-of-order-safe**: re-running an old batch changes nothing.

Final silver state after day 2: 100001 has 330/12, 100002 and 100003 are `_is_deleted = true`,
and 100009 has 410/4. This is asserted in `tests/test_datagen_and_cdc.py` (pure Python) and
`tests/test_spark_silver.py` (Spark).

## 6. The other sources: incremental without log-based CDC

### Oracle ERP: watermark on `LAST_UPDATE_DATE`

```sql
WHERE LAST_UPDATE_DATE >  TO_DATE('{window_start}', 'YYYY-MM-DD HH24:MI:SS')
  AND LAST_UPDATE_DATE <= TO_DATE('{window_end}',   'YYYY-MM-DD HH24:MI:SS')
```

- **Why an upper bound?** `window_end` = `MAX(LAST_UPDATE_DATE)` read *before* copying. Rows committed during the copy are picked up next run, not lost between two moving targets.
- **Requirements.** The column is maintained by a trigger on every insert and update (`sql/source_systems/oracle_erp_setup.sql`) and is indexed.
- **Limitation: hard deletes are invisible.** It is mitigated as follows:
  - ERP orders are soft-deleted (`ORDER_STATUS = 'CANCELLED'`);
  - tiny tables are full-loaded (`erp_plant`);
  - a weekly key-only full compare could flag orphans.
  - With Oracle GoldenGate or LogMiner this would become log-based CDC. It was not licensed in this phase, and ADF has no native Oracle CDC.
- **Clock skew and long transactions.** A transaction that started before the last run but committed after it can carry an *older* `LAST_UPDATE_DATE`. For those tables, the window start can be set back by a safety overlap. Silver's `_sequence` guard makes the re-read rows harmless.

### CMMS REST API: `updated_since` / `updated_until`

- The window is the trigger time minus a **5-minute safety lag**, because the API's search index lags its database.
- The source count comes from the API's own `meta.total_count` for the same filter.
- Pagination runs until a page returns an empty `data` array.

### Supplier files: the file IS the change

- The window is on the file's `LastModified` timestamp.
- Processed files are moved to `archive/`.
- Suppliers re-send lines, so silver dedups on `delivery_id`.

## 7. Where CDC shows up again downstream: Delta Change Data Feed

Databricks has its own CDC for Delta tables: the **Change Data Feed (CDF)**
(`delta.enableChangeDataFeed = true` on silver and gold). It is the same idea as SQL Server CDC:

| SQL Server CDC | Delta CDF |
|---|---|
| `__$start_lsn` | `_commit_version` |
| `__$operation` 1/2/3/4 | `_change_type` delete / insert / update_preimage / update_postimage |
| `fn_cdc_get_all_changes(from, to)` | `readChangeFeed` with `startingVersion` / `endingVersion` |

We use it twice:

- **Gold facts.** Read silver's CDF since the last processed version, and recompute only the affected business dates.
- **Snowflake publish.** Read gold's CDF since the last published version, and MERGE only those rows into Snowflake.

In both cases the version watermark (`control.watermarks`) is advanced only after reconciliation.

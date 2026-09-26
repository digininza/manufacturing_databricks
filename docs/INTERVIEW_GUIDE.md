# Interview guide: Manufacturing Modernization

## 30-second version
> "I worked on a manufacturing modernization platform on Azure. We ingested MES data from
> SQL Server using native CDC, ERP data from Oracle incrementally, maintenance data from a REST API,
> and supplier files from ADLS. All of it ran through metadata-driven ADF pipelines with a control
> table, run history, audit and reconciliation. Machine IoT telemetry streamed through Event Hub into
> Databricks Structured Streaming. On Databricks we built a Medallion architecture: Auto Loader into
> bronze; cleansing, DQ, dedup and CDC merges in silver; and a star schema with SCD2 dimensions in gold.
> Gold was published incrementally to Snowflake, and Power BI consumed it both through an Import
> semantic model and through DirectQuery on governed Snowflake views. Every hop was reconciled, and
> no watermark advanced unless counts and totals matched."

## 2-minute version (walk the diagram in README.md)
1. **Sources and why each pattern.**
   - MES has native CDC, which captures deletes and has a low source impact.
   - For Oracle, GoldenGate wasn't licensed, so we used an indexed, trigger-maintained `LAST_UPDATE_DATE` watermark.
   - The API uses `updated_since` with a safety lag.
   - Files use a LastModified window, then archive.
2. **ADF framework.** A master reads `ctl.ingestion_control`, and a router dispatches to 4 child templates.
   - The window is frozen per run for idempotency.
   - Write-audit-publish through `_staging`.
   - A stored-procedure count gate.
   - The watermark advances last.
   - Rerun-from-failure; retries for network errors; alerting through a Logic App.
3. **Databricks.**
   - Auto Loader into bronze, then the ADF-manifest recon.
   - Silver is streaming `availableNow` with foreachBatch: standardize → DQ/quarantine → dedup → sequence-guarded MERGE → per-batch recon.
   - Gold uses SCD2 plus CDF-driven affected-date recompute.
4. **Serving.** CDF changes → Snowflake STAGE → MERGE → recon. Power BI Import for analysis, DirectQuery views for freshness and shared KPIs, RLS in both.
5. **Streaming.** Event Hub (Kafka endpoint) → bronze raw → silver with a watermark, dedup and 1-minute anomaly windows → OEE fact.

## Deep-dive questions

### Ingestion / ADF
**Q: What does "metadata-driven" mean in your ADF design?**
- The pipelines contain no table names, queries or watermarks.
- `ctl.ingestion_control` holds, per entity, the source type, the query templates with `{window_start}`/`{window_end}` tokens, the watermark type and value, the target folder, tolerances and the owner.
- A master pipeline looks up the pending entities; a Switch routes each one to one of four child pipelines.
- Onboarding a table = one control row + one registry block. There is a CI test that fails if the two disagree.

**Q: How did you make ADF pipelines idempotent?**
Three mechanisms:
1. The window is **frozen** at the first attempt (`usp_start_ingestion_run`), so a retry reuses the same run_id, window, path and file name, and overwrites instead of duplicating.
2. **Write-audit-publish**: the copy goes to `_staging`, and only validated files move to the folder Auto Loader watches.
3. The watermark is advanced **last**, in one transaction, only for a `VALIDATED` run.

Downstream, silver MERGE only accepts a newer `_sequence`, so even a double ingestion converges.

**Q: What if the pipeline fails halfway?**
- The run stays `FAILED`/`VALIDATED` and the watermark is untouched. On-call gets an alert.
- Rerunning the master skips entities already `SUCCESS` for the run date. The failed entity resumes with the same frozen window and overwrites its partial output.
- For hourly CDC, the tumbling-window trigger's self-dependency prevents later hours from running out of order.

**Q: How do you handle network failures?**
- Activity retry policies (3 × 120s) on Copy, Lookup and Web activities, and a 2-node HA self-hosted IR.
- REST calls have timeouts, a request interval and bounded pagination.
- Count validation is deliberately *not* retried: a mismatch is a data problem, not a transient one.

**Q: How did reconciliation work in ADF?**
- An independent `COUNT(*)` on the source for the same frozen window, compared with the Copy activity's `rowsRead`, `rowsCopied` and `rowsSkipped`.
- It runs inside `usp_validate_ingestion_counts`, which THROWs on a mismatch.
- The logic lives in SQL, not ADF expressions: it is one implementation for all sources, it is testable, and it keeps ADF free of nested Ifs (which ADF doesn't allow anyway).

### CDC
**Q: Explain SQL Server CDC.**
- The SQL Agent capture job reads the transaction log into change tables.
- `fn_cdc_get_all_changes_<ci>(from_lsn, to_lsn)` returns each change with `__$start_lsn` (commit position, which is our watermark), `__$seqval` (order within the transaction), and `__$operation` (1 delete, 2 insert, 3/4 update before/after).
- We extract `(last_lsn, max_lsn]`, starting from `fn_cdc_increment_lsn(last_lsn)`.

**Q: What can go wrong with CDC?**
- **LSN gap:** the cleanup job purges changes older than retention. If we were down longer than that, `from_lsn < fn_cdc_get_min_lsn()`, and we **throw instead of silently skipping**. Recovery is a full reload plus an audited watermark reset.
- **First load:** CDC only has changes made since it was enabled, so the first load is a snapshot.
- **Schema changes:** they need a new capture instance.

**Q: How do you apply CDC in the lakehouse?**
- Dedup to the latest version per key by `(lsn, seqval)` inside the batch. One key updated twice in an hour keeps only the last version.
- Then a MERGE with `WHEN MATCHED AND s._sequence > t._sequence`, which is out-of-order-safe and replay-safe.
- Deletes are soft (`_is_deleted`), so the audit trail stays and CDF propagates the delete to gold and Snowflake.

**Q: Oracle has no CDC in your design. What about deletes?**
- Watermark loads can't see hard deletes. ERP orders are soft-deleted by status; tiny tables are full-loaded; a periodic key compare can catch orphans.
- The long-term fix is GoldenGate/LogMiner, which I'd propose if deletes became material.
- There is also an upper-bound freeze: `window_end = MAX(LAST_UPDATE_DATE)` taken *before* the copy, so concurrent commits aren't lost.

### Databricks
**Q: Why Auto Loader?**
It gives exactly-once file tracking in the checkpoint (reruns don't double-load), schema evolution with `addNewColumns`, rescued data for bad values, and `availableNow` for batch economics with streaming bookkeeping.

**Q: Why is silver a streaming job if it runs in batch?**
- `readStream` from bronze plus `trigger(availableNow)` means the **checkpoint is my watermark**.
- `foreachBatch` does DQ, dedup, MERGE and recon. If recon fails, I raise, the offset isn't committed, and the next run replays the same rows automatically.
- Side-writes (quarantine, DQ results) use `txnAppId`/`txnVersion` so replays don't double-append.

**Q: What transformations did you do in silver?**
All of it is driven by config, per column:
- trim / upper / initcap / empty-to-null;
- type casts (a failed cast becomes NULL, which DQ catches);
- renames to the conformed names;
- null defaults.

Then the DQ rules (ERROR → quarantine, WARN → flag), exact and latest-per-key dedup, and the CDC MERGE with soft deletes.

**Q: How do you check data quality?**
- A rule engine where each rule is a SQL predicate that must be TRUE, and NULL counts as a failure.
- ERROR rows go to `quarantine.<entity>` with the failed rule names; WARN rows are flagged.
- Per-rule counts go to `control.dq_results` for trends, and alerts fire on any quarantine.
- Quarantined rows are fixed at source and come back through CDC; they are never hand-patched.

**Q: What reconciliation did you do on Databricks?**
- ADF manifest `rows_copied` = bronze rows per run.
- Bronze in = valid + quarantined + duplicates per batch, plus a target verification that every key landed.
- Silver → gold: count and SUM(units) for the affected dates.
- Gold → Snowflake: count and SUM per table.

Each check gates the next step, and a report task fails the job if any break is open. That blocks the Power BI refresh.

### Modelling
**Q: What was the grain of your facts?**
- `fact_production_daily` = day × machine × product: units, scrap, run minutes, ideal units, inspections, defects.
- `fact_machine_daily` = day × machine, because availability/downtime belongs to the machine; splitting it across products would invent data. OEE lives here.
- Plus transaction facts: inspections, work orders (accumulating snapshot) and supplier deliveries.

**Q: How did you implement SCD2?**
- Hash the tracked attributes. One atomic MERGE using the staged-union trick: changed keys appear twice, once to close the current row and once (with a NULL merge key) to insert the new version.
- New keys start at 1900-01-01, so historical facts resolve.
- Facts join point-in-time: `event_ts >= effective_from AND < effective_to`.

Example: a machine moved line, and last month's line-level OEE stays on the old line. A product's cycle time changed after a tooling change, and historical performance still uses the old standard.

**Q: Surrogate keys: identity or hash?**
Deterministic `xxhash64(business_key, effective_from)`. Rebuilding gold, adding an environment or recovering from DR regenerates identical keys, so Snowflake and Power BI never get orphaned facts. Identity columns depend on insert order.

**Q: How do incremental facts handle late corrections?**
- Read silver's Change Data Feed since the last processed version and collect the affected dates, including pre-images.
- Recompute those dates and MERGE, including `WHEN NOT MATCHED BY SOURCE ... DELETE` for grain rows that disappeared.
- Reconcile, then advance the version watermark.

### Snowflake / Power BI
**Q: For which data did you create views for Power BI, and when did you use the semantic model? Why?**
- **Semantic model (Import on the gold star schema):** the analytical pages (OEE by machine/product/shift, quality drill-downs, trends). These have heavy slicing and time intelligence, and they need to be fast. Incremental refresh keeps 3 years and refreshes the last 10 days.
- **Snowflake views (DirectQuery):**
  - (1) **Shop-floor status**: freshness matters because MES is hourly, and there is one row per machine;
  - (2) **Exec KPIs and scrap cost**: Finance queries the same SQL, so there is one definition and identical numbers;
  - (3) row security must follow the real user via SSO into Snowflake's row access policy.
- **Never:** DirectQuery on large facts. Each visual becomes a warehouse query, which is slow and burns credits.
- `Plant` is Dual so one slicer drives both storage modes.

**Q: How did gold get into Snowflake?**
- The Spark Snowflake connector with key-pair auth.
- Gold CDF changes since the last published version → overwrite a STAGE table → one MERGE (upsert + delete) → reconcile count and sum → advance the watermark.
- Dimensions are published before facts. Separate load and BI warehouses, and a resource monitor.

**Q: How do you avoid Power BI showing half-loaded data?**
The Power BI refresh is the last task of the Databricks job, called via the REST API. It only runs if publish and reconciliation passed. A "Data As Of" measure is shown on every page.

### Security, lineage, ops
**Q: How did you manage credentials?**
- Managed identities for everything in Azure (ADF → ADLS, the control DB, Key Vault and the Databricks API; the Databricks Access Connector → ADLS via Unity Catalog external locations).
- Key Vault for on-prem DB passwords, the API secret, the Snowflake private key and the webhook.
- ADF uses Key Vault linked-service references; Databricks uses a Key Vault-backed secret scope.
- `secureInput`/`secureOutput` on ADF Web activities, 90-day rotation, and nothing in Git.

**Q: How did you implement lineage?**
- Purview for ADF copy lineage, Unity Catalog for automatic table and column lineage.
- Run-level lineage columns (`_ingestion_run_id`, `_source_file`, `_pipeline_run_id`) link any gold row back to the ADF run, frozen window and exact source query.

**Q: What was the hardest production issue?** (use as a story)
- The CDC LSN gap after a long SHIR outage over a holiday weekend. The pipeline threw instead of silently loading from the new min LSN.
- We did a controlled full reload, reset the watermark with an audited ticket, and silver's sequence-guarded MERGE absorbed the reload without duplicates.
- Afterwards we raised CDC retention to 7 days and added an IR-offline alert.

## Numbers to have ready (illustrative, not measured)
These are example figures to size your answer, not measurements from this project:

- ~3 plants, ~40 machines, 8 source entities, ~2M production-log changes per month.
- ~50M sensor readings per day, aggregated to ~60k minute-rows per day.
- Daily batch SLA 06:00 UTC, with the hourly MES window about 10 minutes end to end.

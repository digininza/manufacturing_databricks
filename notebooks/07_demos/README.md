# Demo notebooks: see the framework behave

Run order for a clean demo in `dev`:

1. `00_setup/00_create_catalog_schemas`
2. `00_setup/01_simulate_adf_landing` with `day=1`, then run the job `mfg_batch_pipeline` (or the notebooks 01 → 06 by hand).
3. `00_setup/01_simulate_adf_landing` with `day=2`, then run the pipeline again.
4. Open the demos below.

| Notebook | Shows |
|---|---|
| `demo_01_cdc_walkthrough` | SQL Server CDC rows in bronze (op 1/2/4), how silver applies them. One key updated twice in a batch (the last update wins), soft deletes, and the quarantined row that the source later fixed. |
| `demo_02_scd2_walkthrough` | `dim_machine` / `dim_product` history. A fact joined point-in-time to the version valid on the production day. |
| `demo_03_reconciliation_failure` | A manifest that over-states `rows_copied`, so ADF→Bronze fails, silver is blocked, you fix it, and the rerun passes. |
| `demo_04_idempotent_rerun` | Re-running silver + gold changes nothing: the MERGE metrics show 0 inserted and 0 updated. |

# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze · Auto Loader ingestion + ADF→Bronze reconciliation
# MAGIC For every registry entity (or the one passed as `entity`): ingest new parquet files, then
# MAGIC prove `manifest.rows_copied == bronze rows` per ADF run. A failed reconciliation **fails
# MAGIC this task**, so the downstream silver tasks do not run.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.bronze.autoloader import ingest_entity_to_bronze
from src.bronze.manifest_recon import reconcile_manifests
from src.framework import reconciliation as recon
from src.framework.alerting import send_alert
from src.framework.control import log_run

ENTITY = get_widget("entity", "ALL")
ctx = RunContext(config, "mfg_batch_pipeline", new_run_id("bronze"), JOB_RUN_ID)
audit = AuditLogger(spark, ctx)
entities = [e for n, e in registry.items() if ENTITY in ("ALL", n)]

all_results, failures = [], []
for entity in entities:
    try:
        ingest_entity_to_bronze(spark, config, entity)
        results = reconcile_manifests(spark, ctx, entity)
        recon.persist_results(spark, ctx, results)
        all_results += results
        bad = [r for r in results if r.status != recon.PASSED]
        log_run(spark, ctx, "bronze", entity.name, "RECON_FAILED" if bad else "SUCCESS", {"rows_in": sum(r.target_count for r in results)})
        if bad:
            failures.append(entity.name)
            send_alert(ctx, "CRITICAL", entity.name, "ADF->Bronze count mismatch", {"runs": [r.detail for r in bad]})
    except Exception as exc:  # one broken entity must not stop the others from landing
        failures.append(entity.name)
        log_run(spark, ctx, "bronze", entity.name, "FAILED", error_message=str(exc))
        send_alert(ctx, "CRITICAL", entity.name, f"bronze ingestion failed: {exc}")

audit.log("ALL", "BRONZE_COMPLETED", {"entities": [e.name for e in entities], "failures": failures})
display(
    spark.createDataFrame(
        [(r.entity_name, r.detail, r.source_count, r.target_count, r.status) for r in all_results] or [("-", "-", 0, 0, "-")],
        "entity STRING, run STRING, adf_rows_copied BIGINT, bronze_rows BIGINT, status STRING",
    )
)  # noqa: F821
if failures:
    raise Exception(f"Bronze failed for {failures}: silver will not run until fixed (see control.reconciliation_results)")

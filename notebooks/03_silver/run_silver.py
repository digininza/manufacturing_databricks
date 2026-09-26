# Databricks notebook source
# MAGIC %md
# MAGIC # Silver · cleanse → DQ → dedup → CDC MERGE → reconcile (per micro-batch)
# MAGIC Streaming read of bronze with `availableNow`: processes exactly the rows not yet
# MAGIC committed in the checkpoint, then stops. See `src/silver/processor.py` for the gate logic.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.silver.processor import run_silver_entity

ENTITY = get_widget("entity", "ALL")
ctx = RunContext(config, "mfg_batch_pipeline", new_run_id("silver"), JOB_RUN_ID)
# masters first: reference data is in silver before the transactions that point at it
order = [
    "erp_plant",
    "mes_machine",
    "erp_product",
    "supplier_delivery",
    "erp_production_order",
    "mes_production_log",
    "mes_quality_inspection",
    "cmms_work_order",
]
failures = []
for name in order:
    if ENTITY not in ("ALL", name):
        continue
    try:
        run_silver_entity(spark, ctx, registry[name])
    except Exception as exc:
        failures.append(f"{name}: {exc}")

display(
    spark.sql(
        f"SELECT entity_name, batch_id, status, rows_in, rows_valid, rows_quarantined, rows_duplicate, rows_written FROM {config.fq('control','pipeline_run_log')} WHERE run_id = '{ctx.run_id}' ORDER BY entity_name, batch_id"
    )
)  # noqa: F821
if failures:
    raise Exception("Silver failures (checkpoints NOT advanced, batches will replay): " + " | ".join(failures))

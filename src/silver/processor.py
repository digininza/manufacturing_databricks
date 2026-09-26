"""
Silver processor: bronze.<entity> --(streaming, availableNow)--> silver.<entity>

Every micro-batch runs this exact sequence (foreachBatch):

   bronze rows ─► standardize ─► DQ ─┬─► ERROR rows ─► quarantine.<entity>
                                     └─► valid ─► dedup latest ─► MERGE silver
                                                                   │
                     reconcile: in = valid + quarantined + duplicates ◄┘
                                target verify: every merged key is in silver

If ANY step or reconciliation fails, the function raises. Spark then does NOT
commit the micro-batch offset to the checkpoint, so the next run re-reads the
same bronze rows. That is the streaming equivalent of "only advance the
watermark after a successful, reconciled load". Quarantine/DQ writes use
txnAppId/txnVersion so a replayed batch never appends twice.
"""

from __future__ import annotations

import json
from datetime import datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from src.common.config import EntityConfig
from src.common.logging_utils import get_logger
from src.framework import reconciliation as recon
from src.framework.alerting import send_alert
from src.framework.control import log_run
from src.framework.dq import apply_dq
from src.framework.run_context import RunContext
from src.silver.cdc import build_merge_sql, deduplicate_latest
from src.silver.transformations import add_silver_metadata, standardize

log = get_logger(__name__)


def ensure_silver_table(spark: SparkSession, target: str, sample: DataFrame) -> None:
    if not spark.catalog.tableExists(target):
        sample.limit(0).write.format("delta").saveAsTable(target)
        spark.sql(
            f"ALTER TABLE {target} SET TBLPROPERTIES ("
            "'delta.enableChangeDataFeed' = 'true', "  # gold + Snowflake publish read changes, not full tables
            "'delta.autoOptimize.optimizeWrite' = 'true', "
            "'delta.enableDeletionVectors' = 'true')"
        )


def make_batch_processor(spark: SparkSession, ctx: RunContext, entity: EntityConfig):
    target = ctx.config.fq("silver", entity.silver_table)
    quarantine_table = ctx.config.fq("quarantine", entity.silver_table)

    def process(batch_df: DataFrame, batch_id: int) -> None:
        start = datetime.utcnow()
        rows_in = batch_df.count()
        if rows_in == 0:
            return
        try:
            std = add_silver_metadata(standardize(batch_df, entity), entity, ctx.run_id)
            valid, quarantined, dq_stats = apply_dq(std, entity.dq_rules)
            rows_quarantined = quarantined.count()
            latest, rows_duplicate = deduplicate_latest(valid, entity.primary_keys)
            rows_valid = latest.count()

            results = [recon.check_silver_balance(entity.name, rows_in, rows_valid, rows_quarantined, rows_duplicate)]
            recon.assert_all_passed(results)

            if rows_quarantined:
                (
                    quarantined.withColumn("_quarantined_ts", F.current_timestamp())
                    .withColumn("_batch_id", F.lit(batch_id))
                    .write.format("delta")
                    .mode("append")
                    .option("mergeSchema", "true")
                    .option("txnAppId", f"quarantine_{entity.name}")
                    .option("txnVersion", batch_id)
                    .saveAsTable(quarantine_table)
                )

            latest = latest.drop("cdc_update_mask", "_source_file_ts")
            ensure_silver_table(spark, target, latest)
            latest.createOrReplaceTempView(f"_silver_src_{entity.name}")
            spark.sql(build_merge_sql(target, f"_silver_src_{entity.name}", entity.primary_keys, latest.columns))
            metrics = spark.sql(f"DESCRIBE HISTORY {target} LIMIT 1").collect()[0]["operationMetrics"]
            rows_written = int(metrics.get("numTargetRowsInserted", 0)) + int(metrics.get("numTargetRowsUpdated", 0))

            # target verification: every live key in this batch must now exist in silver at >= this sequence
            silver_now = spark.table(target).select(*entity.primary_keys, F.col("_sequence").alias("_t_seq"))
            missing = (
                latest.filter("_is_deleted = false")
                .join(silver_now, entity.primary_keys, "left")
                .filter(F.col("_t_seq").isNull() | (F.col("_t_seq") < F.col("_sequence")))
                .count()
            )
            results.append(
                recon.compare_counts("SILVER_TARGET_VERIFY", entity.name, 0, missing, 0, detail=f"batch_id={batch_id} keys_missing_in_target={missing}")
            )
            recon.persist_results(spark, ctx, results)
            recon.assert_all_passed(results)

            dq_rows = [
                (ctx.run_id, entity.name, int(batch_id), r["name"], r["severity"], dq_stats.get(r["name"], 0), rows_in, datetime.utcnow())
                for r in entity.dq_rules
            ]
            if dq_rows:
                spark.createDataFrame(
                    dq_rows,
                    "run_id STRING, entity_name STRING, batch_id BIGINT, rule_name STRING, severity STRING, "
                    "failed_count BIGINT, total_count BIGINT, checked_ts TIMESTAMP",
                ).write.format("delta").mode("append").option("txnAppId", f"dq_{entity.name}").option("txnVersion", batch_id).saveAsTable(
                    ctx.config.fq("control", "dq_results")
                )

            m = {
                "rows_in": rows_in,
                "rows_valid": rows_valid,
                "rows_quarantined": rows_quarantined,
                "rows_duplicate": rows_duplicate,
                "rows_written": rows_written,
            }
            log_run(spark, ctx, "silver", entity.name, "SUCCESS", m, batch_id, start_ts=start)
            log.info("silver entity=%s batch=%s %s", entity.name, batch_id, json.dumps(m))
            if rows_quarantined:
                send_alert(ctx, "WARNING", entity.name, f"{rows_quarantined} row(s) quarantined", {"dq_failures": dq_stats})
        except Exception as exc:
            status = "RECON_FAILED" if isinstance(exc, recon.ReconciliationError) else "FAILED"
            log_run(spark, ctx, "silver", entity.name, status, {"rows_in": rows_in}, batch_id, error_message=str(exc), start_ts=start)
            send_alert(ctx, "CRITICAL", entity.name, f"silver batch {batch_id} {status}: {exc}")
            raise  # offset NOT committed -> batch is replayed next run

    return process


def run_silver_entity(spark: SparkSession, ctx: RunContext, entity: EntityConfig) -> None:
    query = (
        spark.readStream.format("delta")
        .option("maxFilesPerTrigger", 200)
        .table(ctx.config.fq("bronze", entity.name))
        .writeStream.foreachBatch(make_batch_processor(spark, ctx, entity))
        .option("checkpointLocation", ctx.config.checkpoint("silver", entity.name))
        .trigger(availableNow=True)
        .queryName(f"silver_{entity.name}")
        .start()
    )
    query.awaitTermination()

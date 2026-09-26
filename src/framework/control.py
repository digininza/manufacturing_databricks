"""
Databricks-side control tables: pipeline run log + version watermarks.

GOLDEN RULE (same as the ADF side): a watermark is advanced ONLY after the batch
has been written AND reconciled. If anything fails, the watermark stays put and
the next run replays the same window. All writes downstream are MERGEs keyed on
business keys, so a replay is harmless (idempotent).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pyspark.sql import SparkSession

from src.framework.run_context import RunContext

RUN_LOG_SCHEMA = (
    "run_id STRING, job_run_id STRING, pipeline_name STRING, layer STRING, entity_name STRING, "
    "batch_id BIGINT, status STRING, rows_in BIGINT, rows_valid BIGINT, rows_quarantined BIGINT, "
    "rows_duplicate BIGINT, rows_written BIGINT, error_message STRING, start_ts TIMESTAMP, end_ts TIMESTAMP"
)


def log_run(
    spark: SparkSession,
    ctx: RunContext,
    layer: str,
    entity_name: str,
    status: str,
    metrics: Optional[Dict[str, int]] = None,
    batch_id: int = -1,
    error_message: str = None,
    start_ts: datetime = None,
) -> None:
    m = metrics or {}
    row = [
        (
            ctx.run_id,
            ctx.job_run_id,
            ctx.pipeline_name,
            layer,
            entity_name,
            int(batch_id),
            status,
            int(m.get("rows_in", 0)),
            int(m.get("rows_valid", 0)),
            int(m.get("rows_quarantined", 0)),
            int(m.get("rows_duplicate", 0)),
            int(m.get("rows_written", 0)),
            (error_message or "")[:4000] or None,
            start_ts or ctx.started_at,
            datetime.utcnow(),
        )
    ]
    spark.createDataFrame(row, RUN_LOG_SCHEMA).write.format("delta").mode("append").saveAsTable(ctx.config.fq("control", "pipeline_run_log"))


def get_version_watermark(spark: SparkSession, ctx: RunContext, consumer: str, source_table: str) -> Optional[int]:
    """Last Delta version of `source_table` fully processed by `consumer` (used with Change Data Feed)."""
    table = ctx.config.fq("control", "watermarks")
    rows = spark.sql(f"SELECT last_version FROM {table} WHERE consumer = '{consumer}' AND source_table = '{source_table}'").collect()
    return rows[0]["last_version"] if rows else None


def advance_version_watermark(spark: SparkSession, ctx: RunContext, consumer: str, source_table: str, version: int) -> None:
    table = ctx.config.fq("control", "watermarks")
    spark.sql(
        f"""
        MERGE INTO {table} t
        USING (SELECT '{consumer}' AS consumer, '{source_table}' AS source_table) s
        ON t.consumer = s.consumer AND t.source_table = s.source_table
        WHEN MATCHED THEN UPDATE SET last_version = {int(version)}, run_id = '{ctx.run_id}', updated_ts = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (consumer, source_table, last_version, run_id, updated_ts)
             VALUES ('{consumer}', '{source_table}', {int(version)}, '{ctx.run_id}', current_timestamp())
        """
    )


def latest_table_version(spark: SparkSession, table: str) -> int:
    return spark.sql(f"DESCRIBE HISTORY {table} LIMIT 1").collect()[0]["version"]

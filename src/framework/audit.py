"""Append-only audit trail: who/what/when for every significant platform event."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from pyspark.sql import SparkSession

from src.framework.run_context import RunContext

AUDIT_SCHEMA = "run_id STRING, job_run_id STRING, pipeline_name STRING, entity_name STRING, event_type STRING, event_detail STRING, event_ts TIMESTAMP"


class AuditLogger:
    def __init__(self, spark: SparkSession, ctx: RunContext):
        self.spark = spark
        self.ctx = ctx
        self.table = ctx.config.fq("control", "audit_log")

    def log(self, entity_name: str, event_type: str, detail: Dict[str, Any] = None) -> None:
        row = [
            (
                self.ctx.run_id,
                self.ctx.job_run_id,
                self.ctx.pipeline_name,
                entity_name,
                event_type,
                json.dumps(detail or {}, default=str),
                datetime.utcnow(),
            )
        ]
        self.spark.createDataFrame(row, AUDIT_SCHEMA).write.format("delta").mode("append").saveAsTable(self.table)

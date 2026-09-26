"""
Reconciliation framework.

Four checkpoints, each one GATES the next step (a failed recon raises, so the
watermark / streaming checkpoint does not advance and the run is replayed):

  ADF_TO_BRONZE     manifest.rows_copied   == bronze rows for that _ingestion_run_id
  BRONZE_TO_SILVER  rows_in == rows_valid + rows_quarantined + rows_duplicate  (nothing silently lost)
  SILVER_TO_GOLD    SUM(units_produced) in silver == SUM in fact for the affected dates
  GOLD_TO_SNOWFLAKE COUNT + SUM(measure) in gold == Snowflake after publish

The comparison logic below is pure Python so it is unit-tested without Spark.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

PASSED = "PASSED"
FAILED = "FAILED"


class ReconciliationError(Exception):
    pass


@dataclass
class ReconResult:
    recon_type: str
    entity_name: str
    source_count: int
    target_count: int
    status: str
    source_amount: Optional[float] = None
    target_amount: Optional[float] = None
    detail: str = ""

    @property
    def difference(self) -> int:
        return self.source_count - self.target_count


def compare_counts(recon_type: str, entity: str, source_count: int, target_count: int, tolerance: int = 0, detail: str = "") -> ReconResult:
    status = PASSED if abs(source_count - target_count) <= tolerance else FAILED
    return ReconResult(recon_type, entity, int(source_count), int(target_count), status, detail=detail)


def compare_counts_and_amounts(
    recon_type: str,
    entity: str,
    source_count: int,
    target_count: int,
    source_amount: float,
    target_amount: float,
    count_tolerance: int = 0,
    amount_tolerance_pct: float = 0.0001,
    detail: str = "",
) -> ReconResult:
    base = compare_counts(recon_type, entity, source_count, target_count, count_tolerance, detail)
    src = float(source_amount or 0)
    tgt = float(target_amount or 0)
    allowed = abs(src) * amount_tolerance_pct
    amount_ok = abs(src - tgt) <= max(allowed, 1e-9)
    base.source_amount, base.target_amount = src, tgt
    if not amount_ok:
        base.status = FAILED
    return base


def check_silver_balance(entity: str, rows_in: int, rows_valid: int, rows_quarantined: int, rows_duplicate: int) -> ReconResult:
    """Every bronze row must be accounted for: written, quarantined, or de-duplicated."""
    accounted = rows_valid + rows_quarantined + rows_duplicate
    return compare_counts(
        "BRONZE_TO_SILVER", entity, rows_in, accounted, 0, detail=f"valid={rows_valid} quarantined={rows_quarantined} duplicate={rows_duplicate}"
    )


def assert_all_passed(results: List[ReconResult]) -> None:
    failed = [r for r in results if r.status != PASSED]
    if failed:
        msg = "; ".join(f"{r.recon_type}/{r.entity_name}: source={r.source_count} target={r.target_count} {r.detail}" for r in failed)
        raise ReconciliationError(f"Reconciliation FAILED -> watermark NOT advanced. {msg}")


RECON_SCHEMA = (
    "recon_id STRING, run_id STRING, recon_type STRING, entity_name STRING, source_count BIGINT, target_count BIGINT, "
    "difference BIGINT, source_amount DOUBLE, target_amount DOUBLE, status STRING, detail STRING, checked_ts TIMESTAMP"
)


def persist_results(spark, ctx, results: List[ReconResult]) -> None:
    if not results:
        return
    rows = [
        (
            uuid.uuid4().hex,
            ctx.run_id,
            r.recon_type,
            r.entity_name,
            r.source_count,
            r.target_count,
            r.difference,
            r.source_amount,
            r.target_amount,
            r.status,
            r.detail,
            datetime.utcnow(),
        )
        for r in results
    ]
    spark.createDataFrame(rows, RECON_SCHEMA).write.format("delta").mode("append").saveAsTable(ctx.config.fq("control", "reconciliation_results"))

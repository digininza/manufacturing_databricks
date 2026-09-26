"""
Delta table maintenance: OPTIMIZE (compaction + clustering / Z-ORDER), VACUUM, statistics.

Why it's needed at scale:
  * Streaming micro-batches and MERGEs create MANY SMALL FILES. Reading 1M x 100 KB files is
    dominated by file-open/list overhead; OPTIMIZE compacts them into ~1 GB files.
  * MERGE with deletion vectors leaves "soft-deleted" rows in files until OPTIMIZE purges them.
  * Data skipping only works if similar values live in the same files -> clustering / Z-ORDER.
  * Every rewrite leaves the OLD files on storage (for time travel). VACUUM deletes them, or
    storage cost grows forever.

Per-table strategy (TABLE_STRATEGIES below):

  OPTIMIZE `OPTIMIZE t`. On a LIQUID-CLUSTERED table (silver entities, gold, control) it
           incrementally clusters only new/unclustered files by the table's clustering keys
           (ZORDER is not allowed there). On an UNCLUSTERED table (bronze batch tables created by
           Auto Loader, the minute aggregates) it does plain bin-packing: many small files -> ~1 GB files.
  ZORDER   Hive-partitioned IoT tables: `OPTIMIZE t WHERE <recent partitions> ZORDER BY (cols)`.
           Only the last N days: older partitions are already optimized and never change, so
           re-optimizing them would rewrite TBs for nothing.
  NONE     tiny tables: skip OPTIMIZE (auto-compaction is enough).

VACUUM retention: 168 h (7 days) — MUST be longer than
  (a) the longest-running query/stream that may still read old files, and
  (b) the time-travel window we promise for audits / rollback (RESTORE TABLE ... VERSION AS OF).
  Never go below 7 days; Delta refuses < 168 h unless a safety check is disabled — don't.
  NOTE: VACUUM also removes the Change Data Feed files older than retention, so the CDF consumers
  (gold facts, Snowflake publish) must never fall more than 7 days behind -> alerted by the job SLA.

On Unity Catalog managed tables, Databricks "Predictive Optimization" can run OPTIMIZE/VACUUM
automatically; this module is the explicit, auditable version (and what you'd explain in an interview).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from pyspark.sql import SparkSession

from src.framework.run_context import RunContext

VACUUM_RETAIN_HOURS = 168


@dataclass
class TableStrategy:
    layer: str
    table: str
    mode: str  # OPTIMIZE | ZORDER | NONE
    zorder_by: List[str] = field(default_factory=list)
    partition_col: str = ""
    recent_days: int = 3  # ZORDER mode: only re-optimize the last N daily partitions
    vacuum: bool = True
    analyze: bool = False  # refresh column stats used by the optimizer (join order, broadcast decisions)


TABLE_STRATEGIES = [
    # ---- IoT: Hive-partitioned by date, the biggest tables -> ZORDER inside recent partitions only
    # bronze raw: queried by date + partition/offset for replays -> ZORDER by eh_partition keeps replays of one partition cheap
    TableStrategy("bronze", "iot_sensor_raw", "ZORDER", ["eh_partition"], partition_col="_ingest_date", recent_days=2),
    # silver readings: queried "machine X, sensor Y, day D" -> ZORDER BY (machine_id, sensor_type).
    # Z-ORDER interleaves both columns' bits so files have tight min/max ranges on BOTH -> skipping works
    # for filters on either column. More than 3-4 ZORDER columns dilutes the benefit.
    TableStrategy("silver", "iot_sensor_reading", "ZORDER", ["machine_id", "sensor_type"], partition_col="event_date", recent_days=3),
    TableStrategy("silver", "iot_sensor_minute_agg", "OPTIMIZE"),  # unclustered -> bin-packing compaction
    # ---- batch bronze (Auto Loader appends one small file set per ADF run -> compaction only; bronze is
    # append-only and read incrementally by silver's stream, so no clustering keys are needed)
    *[
        TableStrategy("bronze", t, "OPTIMIZE")
        for t in (
            "mes_machine",
            "mes_production_log",
            "mes_quality_inspection",
            "erp_plant",
            "erp_product",
            "erp_production_order",
            "cmms_work_order",
            "supplier_delivery",
        )
    ],
    # ---- batch silver (liquid clustered on primary key by src/silver/processor.py)
    *[
        TableStrategy("silver", t, "OPTIMIZE")
        for t in (
            "mes_production_log",
            "mes_quality_inspection",
            "mes_machine",
            "erp_product",
            "erp_production_order",
            "cmms_work_order",
            "supplier_delivery",
        )
    ],
    TableStrategy("silver", "erp_plant", "NONE"),
    # ---- gold (liquid clustered on production_date/machine_id in sql/databricks/02_gold_tables.sql)
    TableStrategy("gold", "fact_production_daily", "OPTIMIZE", analyze=True),
    TableStrategy("gold", "fact_machine_daily", "OPTIMIZE", analyze=True),
    TableStrategy("gold", "fact_process_run_sensor", "OPTIMIZE", analyze=True),
    TableStrategy("gold", "fact_quality_inspection", "OPTIMIZE", analyze=True),
    TableStrategy("gold", "dim_machine", "OPTIMIZE", analyze=True),
    TableStrategy("gold", "dim_product", "OPTIMIZE", analyze=True),
    # ---- control tables: append-heavy logs
    TableStrategy("control", "pipeline_run_log", "OPTIMIZE"),
    TableStrategy("control", "reconciliation_results", "OPTIMIZE"),
]


def optimize_sql(fq_table: str, s: TableStrategy) -> str:
    if s.mode == "OPTIMIZE":
        return f"OPTIMIZE {fq_table}"
    if s.mode == "ZORDER":
        return f"OPTIMIZE {fq_table} WHERE {s.partition_col} >= current_date() - INTERVAL {int(s.recent_days)} DAYS " f"ZORDER BY ({', '.join(s.zorder_by)})"
    return ""


def maintenance_statements(ctx: RunContext) -> List[str]:
    """All statements for one run, in order (pure function -> unit tested)."""
    stmts = []
    for s in TABLE_STRATEGIES:
        fq = ctx.config.fq(s.layer, s.table)
        if s.mode != "NONE":
            stmts.append(optimize_sql(fq, s))
        if s.vacuum:
            stmts.append(f"VACUUM {fq} RETAIN {VACUUM_RETAIN_HOURS} HOURS")
        if s.analyze:
            stmts.append(f"ANALYZE TABLE {fq} COMPUTE STATISTICS FOR ALL COLUMNS")
    return stmts


def run_maintenance(spark: SparkSession, ctx: RunContext) -> List[dict]:
    results = []
    for stmt in maintenance_statements(ctx):
        table = stmt.split()[1] if not stmt.startswith("ANALYZE") else stmt.split()[2]
        if not spark.catalog.tableExists(table):
            continue
        try:
            out = spark.sql(stmt).collect()
            results.append({"statement": stmt, "status": "OK", "metrics": str(out[0].asDict())[:500] if out else ""})
        except Exception as exc:  # one table failing must not stop maintenance of the others
            results.append({"statement": stmt, "status": "FAILED", "metrics": str(exc)[:500]})
    return results

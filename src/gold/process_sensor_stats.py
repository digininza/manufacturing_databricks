"""
gold.fact_process_run_sensor — process conditions (temperature, vibration, ...) DURING each production run.
GRAIN: one row per production run (production_log_id) x sensor_type.

Business use: quality engineers correlate defects with process conditions
("runs where spindle temperature exceeded 90 C had 3x the POROSITY defects").

This is the heaviest join in the platform and the textbook SKEW case:

    silver.iot_sensor_reading   ~50M rows/day  -> ~18B rows/year  (BIG)
    silver.mes_production_log   ~3k runs/day   -> ~1M runs/year   (SMALL/MEDIUM)
    join key: machine_id  — only ~40 distinct values, and the high-speed presses emit
              10x more readings than CNC mills  ->  heavy, uneven skew
    + a RANGE condition: event_ts BETWEEN run.start_ts AND run.end_ts

Two execution strategies, chosen by the job's scale:

  INCREMENTAL (daily, a few affected dates):
      runs for those dates = a few thousand rows -> BROADCAST them.
      Readings never shuffle; skew is irrelevant. Cheapest possible plan.

  BACKFILL / FULL REBUILD (dates=None, years of data):
      ~1M runs x wide columns can exceed the broadcast limit, so Spark would fall back to a
      sort-merge join on machine_id -> ~40 shuffle partitions carry ALL data and the press
      machines' partitions are 10x the others: a few tasks run for hours.
      -> SALTED JOIN (src/common/performance.salted_join): readings get salt 0..31, runs are
         replicated 32x, join on (machine_id, salt). Each hot machine is now spread over 32 tasks.
      Why not rely on AQE skewJoin? AQE splits skewed partitions of EQUI-joins after a shuffle;
      here the plan choice + range predicate make it unreliable, and 40 keys means even the
      "median" partition is huge — AQE's skew factor (5x median) never triggers.

After the join, the aggregation is by production_log_id (≈1M distinct, evenly sized) -> no skew.
"""

from __future__ import annotations

from datetime import timedelta
from typing import List, Optional

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from src.common.performance import salted_join
from src.framework.run_context import RunContext

SALT_BUCKETS = 32


def build_fact_process_run_sensor(spark: SparkSession, ctx: RunContext, dates: Optional[List]) -> None:
    from src.gold.facts import _date_filter, merge_fact  # local import: avoid a circular import

    cfg = ctx.config
    readings_tbl = cfg.fq("silver", "iot_sensor_reading")
    if not spark.catalog.tableExists(readings_tbl):
        return  # streaming not deployed in this environment

    runs = (
        spark.table(cfg.fq("silver", "mes_production_log"))
        .filter("_is_deleted = false AND end_ts IS NOT NULL")
        .withColumn("production_date", F.to_date("start_ts"))
        .filter(_date_filter("production_date", dates))
        .select("production_log_id", "machine_id", "product_id", "production_date", "start_ts", "end_ts")
    )

    readings = spark.table(readings_tbl).select("machine_id", "sensor_type", "reading_value", "event_ts", "event_date")
    if dates is not None:
        # PARTITION PRUNING: silver.iot_sensor_reading is physically partitioned by event_date, so this
        # filter makes Spark list/read ONLY those date folders (e.g. 3 of 1,000 partitions) instead of
        # the whole table. +1 day because a C shift that starts on day D ends on D+1.
        read_days = sorted({d for day in dates for d in (day, day + timedelta(days=1))})
        readings = readings.filter(_date_filter("event_date", read_days))
        joined = readings.join(F.broadcast(runs), "machine_id")  # incremental: small side -> broadcast
    else:
        joined = salted_join(readings, runs, "machine_id", salt_buckets=SALT_BUCKETS)  # backfill: salt the skew

    in_run = joined.filter((F.col("event_ts") >= F.col("start_ts")) & (F.col("event_ts") <= F.col("end_ts")))
    fact = in_run.groupBy("production_log_id", "production_date", "machine_id", "product_id", "sensor_type").agg(
        F.count(F.lit(1)).alias("reading_count"),
        F.avg("reading_value").cast("decimal(12,3)").alias("avg_value"),
        F.min("reading_value").cast("decimal(12,3)").alias("min_value"),
        F.max("reading_value").cast("decimal(12,3)").alias("max_value"),
        F.stddev("reading_value").cast("decimal(12,3)").alias("stddev_value"),
    )
    fact = fact.withColumn("date_key", F.date_format("production_date", "yyyyMMdd").cast("int"))
    merge_fact(spark, fact, cfg.fq("gold", "fact_process_run_sensor"), ["production_log_id", "sensor_type"], "production_date", dates, ctx.run_id)

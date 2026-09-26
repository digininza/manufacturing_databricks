"""
Gold facts — incremental via Delta Change Data Feed (CDF).

Fact tables and their GRAIN (the most important modelling decision):

  fact_production_daily     one row per production_date x machine x product
                            measures: units_produced / scrapped / good, run_minutes,
                            ideal_units, inspections, sampled_units, defect_units, failed_inspections
  fact_machine_daily        one row per production_date x machine   (OEE lives here)
                            measures: planned/run/downtime minutes, availability,
                            performance, quality, oee, sensor anomaly minutes, avg temp, max vibration
  fact_quality_inspection   one row per inspection (transaction grain, drill-through)
  fact_maintenance_event    one row per work order (accumulating snapshot)
  fact_supplier_delivery    one row per delivery line

Production day = date of the shift START (a C shift 22:00-06:00 belongs to the day it started).

Incremental strategy ("affected partitions"):
  1. read CDF of the silver sources since the version last processed (control.watermarks)
  2. collect the business dates touched (pre- AND post-images, so a row whose date
     changed fixes both dates; soft deletes included)
  3. recompute ONLY those dates from current silver
  4. MERGE on the grain key: update / insert / and DELETE grain rows that no longer exist
     (WHEN NOT MATCHED BY SOURCE restricted to the affected dates)
  5. reconcile silver vs gold for those dates, THEN advance the version watermark.
Re-running is safe: the same dates are recomputed to the same answer.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from src.framework import reconciliation as recon
from src.framework.control import advance_version_watermark, get_version_watermark, latest_table_version
from src.framework.run_context import RunContext
from src.gold.scd2 import point_in_time_join

CONSUMER = "gold_facts"


# ----------------------------------------------------------------------------- CDF helpers
def changed_rows(spark: SparkSession, ctx: RunContext, silver: str) -> Tuple[Optional[DataFrame], int, Optional[int]]:
    """Returns (changes or None for 'full rebuild', latest version, previous watermark)."""
    table = ctx.config.fq("silver", silver)
    latest = latest_table_version(spark, table)
    last = get_version_watermark(spark, ctx, CONSUMER, table)
    if last is None:
        return None, latest, None
    if last >= latest:
        return spark.table(table).limit(0), latest, last
    return (
        spark.read.format("delta").option("readChangeFeed", "true").option("startingVersion", last + 1).option("endingVersion", latest).table(table),
        latest,
        last,
    )


def affected_dates(changes: Optional[DataFrame], date_expr: str) -> Optional[List]:
    if changes is None:
        return None  # full rebuild
    # pre-images included on purpose: if a run moved from day X to day Y, BOTH days are recomputed
    return [r["d"] for r in changes.select(F.expr(date_expr).alias("d")).distinct().collect() if r["d"]]


def union_dates(*date_lists) -> Optional[List]:
    if any(d is None for d in date_lists):
        return None
    out = set()
    for d in date_lists:
        out.update(d)
    return sorted(out)


def _date_filter(col: str, dates: Optional[List]) -> str:
    if dates is None:
        return "true"
    if not dates:
        return "false"
    return f"{col} IN (" + ", ".join(f"DATE'{d}'" for d in dates) + ")"


def merge_fact(
    spark: SparkSession, df: DataFrame, target: str, grain: List[str], date_col: Optional[str], dates: Optional[List], run_id: str
) -> Dict[str, int]:
    df = df.withColumn("_pipeline_run_id", F.lit(run_id)).withColumn("_gold_updated_ts", F.current_timestamp())
    df.createOrReplaceTempView("_fact_src")
    cols = df.columns
    on = " AND ".join(f"t.{g} <=> s.{g}" for g in grain)
    not_by_source = ""
    if date_col:
        not_by_source = f"WHEN NOT MATCHED BY SOURCE AND {_date_filter('t.' + date_col, dates)} THEN DELETE"
    spark.sql(
        f"""
        MERGE INTO {target} t USING _fact_src s ON {on}
        WHEN MATCHED THEN UPDATE SET {", ".join(f"t.{c} = s.{c}" for c in cols)}
        WHEN NOT MATCHED THEN INSERT ({", ".join(cols)}) VALUES ({", ".join("s." + c for c in cols)})
        {not_by_source}
        """
    )
    m = spark.sql(f"DESCRIBE HISTORY {target} LIMIT 1").collect()[0]["operationMetrics"]
    return {k: int(m.get(k, 0)) for k in ("numTargetRowsInserted", "numTargetRowsUpdated", "numTargetRowsDeleted")}


# ----------------------------------------------------------------------------- fact_production_daily
def build_fact_production_daily(spark: SparkSession, ctx: RunContext, dates: Optional[List]) -> recon.ReconResult:
    cfg = ctx.config
    prod = spark.table(cfg.fq("silver", "mes_production_log")).filter("_is_deleted = false").withColumn("production_date", F.to_date("start_ts"))
    prod = prod.filter(_date_filter("production_date", dates))
    insp = spark.table(cfg.fq("silver", "mes_quality_inspection")).filter("_is_deleted = false")

    runs = prod.groupBy("production_date", "machine_id", "product_id").agg(
        F.min("start_ts").alias("first_run_start_ts"),
        F.count(F.lit(1)).alias("production_runs"),
        F.sum("units_produced").alias("units_produced"),
        F.sum("units_scrapped").alias("units_scrapped"),
        F.sum((F.unix_timestamp("end_ts") - F.unix_timestamp("start_ts")) / 60).cast("decimal(12,2)").alias("run_minutes"),
    )
    quality = (
        prod.select("production_log_id", "production_date", "machine_id", "product_id")
        .join(insp.select("production_log_id", "sample_size", "defect_count", "inspection_result"), "production_log_id")
        .groupBy("production_date", "machine_id", "product_id")
        .agg(
            F.count(F.lit(1)).alias("inspections"),
            F.sum("sample_size").alias("sampled_units"),
            F.sum("defect_count").alias("defect_units"),
            F.sum(F.when(F.col("inspection_result") == "FAIL", 1).otherwise(0)).alias("failed_inspections"),
        )
    )
    fact = runs.join(quality, ["production_date", "machine_id", "product_id"], "left").na.fill(
        0, ["inspections", "sampled_units", "defect_units", "failed_inspections"]
    )

    dim_machine = spark.table(cfg.fq("gold", "dim_machine"))
    dim_product = spark.table(cfg.fq("gold", "dim_product"))
    fact = point_in_time_join(fact, dim_machine, "machine_id", "first_run_start_ts", "machine_sk")
    fact = point_in_time_join(fact, dim_product, "product_id", "first_run_start_ts", "product_sk")
    # ideal units use the product's standard cycle time AS OF the production day (SCD2 pays off here)
    std = dim_product.select(F.col("product_sk").alias("_psk"), "std_cycle_time_sec")
    plant = dim_machine.select(F.col("machine_sk").alias("_msk"), "plant_id")
    fact = (
        fact.join(std, F.col("product_sk") == F.col("_psk"), "left")
        .join(plant, F.col("machine_sk") == F.col("_msk"), "left")
        .join(spark.table(cfg.fq("gold", "dim_plant")).select("plant_id", "plant_sk"), "plant_id", "left")
        .withColumn("plant_sk", F.coalesce("plant_sk", F.lit(-1).cast("bigint")))
        .withColumn("ideal_units", F.when(F.col("std_cycle_time_sec") > 0, (F.col("run_minutes") * 60 / F.col("std_cycle_time_sec")).cast("int")))
        .withColumn("units_good", F.col("units_produced") - F.col("units_scrapped"))
        .withColumn("date_key", F.date_format("production_date", "yyyyMMdd").cast("int"))
        .select(
            "date_key",
            "production_date",
            "plant_sk",
            "machine_sk",
            "product_sk",
            "machine_id",
            "product_id",
            "production_runs",
            "units_produced",
            "units_scrapped",
            "units_good",
            "run_minutes",
            "ideal_units",
            "inspections",
            "sampled_units",
            "defect_units",
            "failed_inspections",
        )
    )
    target = cfg.fq("gold", "fact_production_daily")
    merge_fact(spark, fact, target, ["production_date", "machine_id", "product_id"], "production_date", dates, ctx.run_id)

    # SILVER_TO_GOLD reconciliation: counts of runs and SUM(units_produced) for the affected dates
    s = prod.agg(F.count(F.lit(1)).alias("c"), F.coalesce(F.sum("units_produced"), F.lit(0)).alias("u")).collect()[0]
    g = (
        spark.table(target)
        .filter(_date_filter("production_date", dates))
        .agg(F.coalesce(F.sum("production_runs"), F.lit(0)).alias("c"), F.coalesce(F.sum("units_produced"), F.lit(0)).alias("u"))
        .collect()[0]
    )
    return recon.compare_counts_and_amounts(
        "SILVER_TO_GOLD", "fact_production_daily", s["c"], g["c"], s["u"], g["u"], detail=f"dates={'ALL' if dates is None else len(dates)}"
    )


# ----------------------------------------------------------------------------- fact_machine_daily (OEE)
def build_fact_machine_daily(spark: SparkSession, ctx: RunContext, dates: Optional[List]) -> None:
    cfg = ctx.config
    planned_per_day = int(cfg.get("gold.planned_minutes_per_shift")) * int(cfg.get("gold.shifts_per_day"))
    prod_fact = spark.table(cfg.fq("gold", "fact_production_daily")).filter(_date_filter("production_date", dates))
    base = prod_fact.groupBy("production_date", "machine_id").agg(
        F.sum("run_minutes").alias("run_minutes"),
        F.sum("ideal_units").alias("ideal_units"),
        F.sum("units_produced").alias("units_produced"),
        F.sum("units_good").alias("units_good"),
    )
    downtime = (
        spark.table(cfg.fq("silver", "cmms_work_order"))
        .filter("_is_deleted = false AND status = 'COMPLETED'")
        .withColumn("production_date", F.to_date("reported_at"))
        .filter(_date_filter("production_date", dates))
        .groupBy("production_date", "machine_id")
        .agg(
            F.sum("downtime_minutes").alias("downtime_minutes"),
            F.sum(F.when(F.col("work_type") == "BREAKDOWN", 1).otherwise(0)).alias("breakdowns"),
        )
    )
    sensor_tbl = cfg.fq("silver", "iot_sensor_minute_agg")
    if spark.catalog.tableExists(sensor_tbl):
        sensors = (
            spark.table(sensor_tbl)
            .filter(_date_filter("event_date", dates))
            .groupBy(F.col("event_date").alias("production_date"), "machine_id")
            .agg(
                F.sum(F.when(F.col("is_anomaly"), 1).otherwise(0)).alias("anomaly_minutes"),
                F.avg(F.when(F.col("sensor_type") == "temperature", F.col("avg_value"))).cast("decimal(8,2)").alias("avg_temperature_c"),
                F.max(F.when(F.col("sensor_type") == "vibration", F.col("max_value"))).cast("decimal(8,3)").alias("max_vibration_mm_s"),
            )
        )
    else:
        sensors = None

    # a machine that was broken down ALL day produced nothing but must still get an OEE row (OEE = 0)
    keys = base.select("production_date", "machine_id").unionByName(downtime.select("production_date", "machine_id")).distinct()
    fact = keys.join(base, ["production_date", "machine_id"], "left").join(downtime, ["production_date", "machine_id"], "left")
    fact = fact.withColumn("_day_ts", F.to_timestamp("production_date"))
    dim_machine = spark.table(cfg.fq("gold", "dim_machine"))
    fact = point_in_time_join(fact, dim_machine, "machine_id", "_day_ts", "machine_sk").drop("_day_ts")
    fact = (
        fact.join(dim_machine.select(F.col("machine_sk").alias("_msk"), "plant_id"), F.col("machine_sk") == F.col("_msk"), "left")
        .join(spark.table(cfg.fq("gold", "dim_plant")).select("plant_id", "plant_sk"), "plant_id", "left")
        .withColumn("plant_sk", F.coalesce("plant_sk", F.lit(-1).cast("bigint")))
        .drop("_msk", "plant_id")
    )
    if sensors is not None:
        fact = fact.join(sensors, ["production_date", "machine_id"], "left")
    else:
        fact = fact.withColumn("anomaly_minutes", F.lit(0)).withColumn("avg_temperature_c", F.lit(None).cast("decimal(8,2)"))
        fact = fact.withColumn("max_vibration_mm_s", F.lit(None).cast("decimal(8,3)"))

    fact = (
        fact.na.fill(0, ["downtime_minutes", "breakdowns", "anomaly_minutes", "run_minutes", "ideal_units", "units_produced", "units_good"])
        .withColumn("planned_minutes", F.lit(planned_per_day))
        # availability = time the machine was available to run / planned time
        .withColumn("available_minutes", F.greatest(F.lit(0), F.col("planned_minutes") - F.col("downtime_minutes")))
        .withColumn("availability", F.round(F.col("available_minutes") / F.col("planned_minutes"), 4))
        # performance = actual output vs what the standard cycle time allows in the run time (capped at 1)
        .withColumn("performance", F.round(F.least(F.lit(1.0), F.col("units_produced") / F.when(F.col("ideal_units") != 0, F.col("ideal_units"))), 4))
        .withColumn("quality", F.round(F.col("units_good") / F.when(F.col("units_produced") != 0, F.col("units_produced")), 4))
        .withColumn("oee", F.round(F.col("availability") * F.col("performance") * F.col("quality"), 4))
        .withColumn("date_key", F.date_format("production_date", "yyyyMMdd").cast("int"))
        .select(
            "date_key",
            "production_date",
            "plant_sk",
            "machine_sk",
            "machine_id",
            "planned_minutes",
            "run_minutes",
            "downtime_minutes",
            "breakdowns",
            "available_minutes",
            "ideal_units",
            "units_produced",
            "units_good",
            "availability",
            "performance",
            "quality",
            "oee",
            "anomaly_minutes",
            "avg_temperature_c",
            "max_vibration_mm_s",
        )
    )
    merge_fact(spark, fact, cfg.fq("gold", "fact_machine_daily"), ["production_date", "machine_id"], "production_date", dates, ctx.run_id)


# ----------------------------------------------------------------------------- transaction / snapshot facts
def build_fact_quality_inspection(spark: SparkSession, ctx: RunContext) -> None:
    cfg = ctx.config
    insp = spark.table(cfg.fq("silver", "mes_quality_inspection")).filter("_is_deleted = false")
    f = point_in_time_join(insp, spark.table(cfg.fq("gold", "dim_machine")), "machine_id", "inspection_ts", "machine_sk")
    f = point_in_time_join(f, spark.table(cfg.fq("gold", "dim_product")), "product_id", "inspection_ts", "product_sk")
    f = f.select(
        "inspection_id",
        F.date_format("inspection_ts", "yyyyMMdd").cast("int").alias("date_key"),
        "machine_sk",
        "product_sk",
        F.coalesce("defect_code", F.lit("NONE")).alias("defect_code"),
        "production_log_id",
        "inspection_ts",
        "sample_size",
        "defect_count",
        "inspection_result",
        "inspector_id",
    )
    merge_fact(spark, f, cfg.fq("gold", "fact_quality_inspection"), ["inspection_id"], None, None, ctx.run_id)
    # hard-remove inspections soft-deleted in silver
    spark.sql(
        f"""DELETE FROM {cfg.fq("gold", "fact_quality_inspection")} WHERE inspection_id IN
            (SELECT inspection_id FROM {cfg.fq("silver", "mes_quality_inspection")} WHERE _is_deleted = true)"""
    )


def build_fact_maintenance_event(spark: SparkSession, ctx: RunContext) -> None:
    cfg = ctx.config
    wo = spark.table(cfg.fq("silver", "cmms_work_order")).filter("_is_deleted = false")
    f = point_in_time_join(wo, spark.table(cfg.fq("gold", "dim_machine")), "machine_id", "reported_at", "machine_sk")
    f = f.select(
        "work_order_id",
        F.date_format("reported_at", "yyyyMMdd").cast("int").alias("reported_date_key"),
        F.date_format("completed_at", "yyyyMMdd").cast("int").alias("completed_date_key"),
        "machine_sk",
        "machine_id",
        "work_type",
        "priority",
        "status",
        "reported_at",
        "started_at",
        "completed_at",
        "downtime_minutes",
        ((F.unix_timestamp("started_at") - F.unix_timestamp("reported_at")) / 60).cast("int").alias("response_minutes"),
        ((F.unix_timestamp("completed_at") - F.unix_timestamp("reported_at")) / 60).cast("int").alias("repair_minutes"),
        "technician",
    )
    merge_fact(spark, f, cfg.fq("gold", "fact_maintenance_event"), ["work_order_id"], None, None, ctx.run_id)


def build_fact_supplier_delivery(spark: SparkSession, ctx: RunContext) -> None:
    cfg = ctx.config
    d = spark.table(cfg.fq("silver", "supplier_delivery")).filter("_is_deleted = false")
    f = (
        d.join(spark.table(cfg.fq("gold", "dim_supplier")).select("supplier_id", "supplier_sk"), "supplier_id", "left")
        .join(spark.table(cfg.fq("gold", "dim_plant")).select("plant_id", "plant_sk"), "plant_id", "left")
        .select(
            "delivery_id",
            F.date_format("delivery_date", "yyyyMMdd").cast("int").alias("date_key"),
            F.coalesce("supplier_sk", F.lit(-1).cast("bigint")).alias("supplier_sk"),
            F.coalesce("plant_sk", F.lit(-1).cast("bigint")).alias("plant_sk"),
            "material_code",
            "lot_number",
            "delivered_qty",
            "rejected_qty",
            (F.col("delivered_qty") - F.col("rejected_qty")).alias("accepted_qty"),
        )
    )
    merge_fact(spark, f, cfg.fq("gold", "fact_supplier_delivery"), ["delivery_id"], None, None, ctx.run_id)


# ----------------------------------------------------------------------------- orchestration
def build_all_facts(spark: SparkSession, ctx: RunContext) -> List[recon.ReconResult]:
    cfg = ctx.config
    prod_changes, prod_v, _ = changed_rows(spark, ctx, "mes_production_log")
    insp_changes, insp_v, _ = changed_rows(spark, ctx, "mes_quality_inspection")
    wo_changes, wo_v, _ = changed_rows(spark, ctx, "cmms_work_order")

    prod_dates = affected_dates(prod_changes, "to_date(start_ts)")
    # an inspection change affects the production day of its production run
    insp_dates = None
    if insp_changes is not None:
        prod_all = spark.table(cfg.fq("silver", "mes_production_log")).select("production_log_id", F.to_date("start_ts").alias("d"))
        insp_dates = [r["d"] for r in insp_changes.select("production_log_id").distinct().join(prod_all, "production_log_id").select("d").distinct().collect()]
    wo_dates = affected_dates(wo_changes, "to_date(reported_at)")
    dates = union_dates(prod_dates, insp_dates, wo_dates)

    results = [build_fact_production_daily(spark, ctx, dates)]
    recon.persist_results(spark, ctx, results)
    recon.assert_all_passed(results)  # raise BEFORE any watermark moves

    build_fact_machine_daily(spark, ctx, dates)
    build_fact_quality_inspection(spark, ctx)
    build_fact_maintenance_event(spark, ctx)
    build_fact_supplier_delivery(spark, ctx)

    for silver, version in (("mes_production_log", prod_v), ("mes_quality_inspection", insp_v), ("cmms_work_order", wo_v)):
        advance_version_watermark(spark, ctx, CONSUMER, cfg.fq("silver", silver), version)
    return results

"""
Gold dimensions.

  dim_machine      SCD2  (line moves, status changes, capacity re-rating must NOT rewrite history)
  dim_product      SCD2  (unit cost / standard cycle time changes -> historic OEE & scrap cost stay correct)
  dim_plant        SCD1  (name corrections: history irrelevant)
  dim_supplier     SCD1
  dim_date         static, generated
  dim_shift        static seed (sql/databricks/04_gold_tables.sql)
  dim_defect_type  static seed
"""

from __future__ import annotations

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from src.framework.run_context import RunContext
from src.gold.scd2 import scd2_merge


def build_dim_machine(spark: SparkSession, ctx: RunContext) -> None:
    cfg = ctx.config
    src = spark.table(cfg.fq("silver", "mes_machine")).select(
        "machine_id",
        "machine_name",
        "plant_id",
        "line_id",
        "machine_type",
        "manufacturer",
        "install_date",
        "status",
        "rated_units_per_hour",
        F.col("_is_deleted").alias("is_source_deleted"),
        F.coalesce("source_modified_at", "cdc_commit_ts").alias("change_ts"),
    )
    tracked = ["machine_name", "plant_id", "line_id", "machine_type", "manufacturer", "install_date", "status", "rated_units_per_hour", "is_source_deleted"]
    scd2_merge(spark, src, cfg.fq("gold", "dim_machine"), ["machine_id"], tracked, "change_ts", "machine_sk", ctx.run_id)


def build_dim_product(spark: SparkSession, ctx: RunContext) -> None:
    cfg = ctx.config
    src = spark.table(cfg.fq("silver", "erp_product")).select(
        "product_id", "sku", "product_name", "product_family", "unit_cost", "std_cycle_time_sec", F.col("last_update_date").alias("change_ts")
    )
    tracked = ["sku", "product_name", "product_family", "unit_cost", "std_cycle_time_sec"]
    scd2_merge(spark, src, cfg.fq("gold", "dim_product"), ["product_id"], tracked, "change_ts", "product_sk", ctx.run_id)


def _scd1_merge(spark: SparkSession, source_view: str, target: str, key: str, cols, sk_col: str, run_id: str) -> None:
    sets = ", ".join(f"t.{c} = s.{c}" for c in cols)
    spark.sql(
        f"""
        MERGE INTO {target} t USING {source_view} s ON t.{key} = s.{key}
        WHEN MATCHED AND sha2(concat_ws('||', {", ".join(f"coalesce(cast(t.{c} as string),'~')" for c in cols)}), 256)
                      <> sha2(concat_ws('||', {", ".join(f"coalesce(cast(s.{c} as string),'~')" for c in cols)}), 256)
            THEN UPDATE SET {sets}, t._pipeline_run_id = '{run_id}', t._gold_updated_ts = current_timestamp()
        WHEN NOT MATCHED THEN INSERT ({sk_col}, {key}, {", ".join(cols)}, _pipeline_run_id, _gold_updated_ts)
            VALUES (xxhash64(s.{key}), s.{key}, {", ".join("s." + c for c in cols)}, '{run_id}', current_timestamp())
        """
    )


def build_dim_plant(spark: SparkSession, ctx: RunContext) -> None:
    spark.table(ctx.config.fq("silver", "erp_plant")).filter("_is_deleted = false").createOrReplaceTempView("_plant_src")
    _scd1_merge(spark, "_plant_src", ctx.config.fq("gold", "dim_plant"), "plant_id", ["plant_name", "city", "country_code", "timezone"], "plant_sk", ctx.run_id)


def build_dim_supplier(spark: SparkSession, ctx: RunContext) -> None:
    spark.sql(
        f"""
        SELECT supplier_id, max_by(supplier_name, _sequence) AS supplier_name
        FROM {ctx.config.fq("silver", "supplier_delivery")} WHERE _is_deleted = false GROUP BY supplier_id
        """
    ).createOrReplaceTempView("_supplier_src")
    _scd1_merge(spark, "_supplier_src", ctx.config.fq("gold", "dim_supplier"), "supplier_id", ["supplier_name"], "supplier_sk", ctx.run_id)


def build_dim_date(spark: SparkSession, ctx: RunContext, start: str = "2024-01-01", end: str = "2030-12-31") -> None:
    target = ctx.config.fq("gold", "dim_date")
    spark.sql(
        f"""
        MERGE INTO {target} t
        USING (
            SELECT CAST(date_format(d, 'yyyyMMdd') AS INT) AS date_key, d AS calendar_date,
                   year(d) AS year, quarter(d) AS quarter, month(d) AS month, date_format(d, 'MMMM') AS month_name,
                   weekofyear(d) AS iso_week, dayofweek(d) AS day_of_week, date_format(d, 'EEEE') AS day_name,
                   dayofweek(d) IN (1, 7) AS is_weekend,
                   concat('FY', CASE WHEN month(d) >= 4 THEN year(d) + 1 ELSE year(d) END) AS fiscal_year
            FROM (SELECT explode(sequence(DATE'{start}', DATE'{end}', INTERVAL 1 DAY)) AS d)
        ) s ON t.date_key = s.date_key
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def build_all_dimensions(spark: SparkSession, ctx: RunContext) -> None:
    build_dim_date(spark, ctx)
    build_dim_plant(spark, ctx)
    build_dim_supplier(spark, ctx)
    build_dim_machine(spark, ctx)
    build_dim_product(spark, ctx)

"""
Gold (Delta) -> Snowflake publish, using the Spark Snowflake connector
(bundled with Databricks Runtime: format("snowflake")).

Why Snowflake at all when gold is already in Databricks? The enterprise BI
estate (Power BI, finance models, other business units' data) is standardised
on Snowflake. Databricks is the engineering platform; Snowflake is the serving
warehouse. Gold in Delta stays the single source of truth; Snowflake is a
published COPY that is reconciled after every publish.

Per table, every run:
  1. read the gold table's Change Data Feed since the last published version
     (first run / after a reset: full snapshot)
  2. collapse to the latest change per key; split upserts vs deletes
  3. write the changes to STAGE.<TABLE>_CHANGES   (connector, overwrite -> retry-safe)
  4. run ONE MERGE in Snowflake (upsert + delete)  (Utils.runQuery, single transaction)
  5. reconcile: row count + SUM(measure) Delta vs Snowflake
  6. only then advance control.watermarks for consumer 'snowflake_publish'

Auth: key-pair (RSA private key from the Key Vault-backed secret scope) for a
Snowflake SERVICE user. No passwords anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from src.common.notebook_utils import get_secret
from src.framework import reconciliation as recon
from src.framework.control import advance_version_watermark, get_version_watermark, latest_table_version
from src.framework.run_context import RunContext

CONSUMER = "snowflake_publish"


@dataclass
class PublishSpec:
    table: str
    keys: List[str]
    measure: Optional[str] = None  # summed for the amount reconciliation


PUBLISH_SPECS = [
    PublishSpec("dim_date", ["date_key"]),
    PublishSpec("dim_plant", ["plant_sk"]),
    PublishSpec("dim_supplier", ["supplier_sk"]),
    PublishSpec("dim_shift", ["shift_code"]),
    PublishSpec("dim_defect_type", ["defect_code"]),
    PublishSpec("dim_machine", ["machine_sk"]),
    PublishSpec("dim_product", ["product_sk"]),
    PublishSpec("fact_production_daily", ["production_date", "machine_id", "product_id"], "units_produced"),
    PublishSpec("fact_machine_daily", ["production_date", "machine_id"], "run_minutes"),
    PublishSpec("fact_quality_inspection", ["inspection_id"], "defect_count"),
    PublishSpec("fact_maintenance_event", ["work_order_id"], "downtime_minutes"),
    PublishSpec("fact_supplier_delivery", ["delivery_id"], "delivered_qty"),
]


def snowflake_options(ctx: RunContext, schema: str = None) -> Dict[str, str]:
    c = ctx.config
    pem = get_secret(c.get("snowflake.secret_scope"), c.get("snowflake.private_key_secret"))
    return {
        "sfURL": c.get("snowflake.url"),
        "sfUser": c.get("snowflake.user"),
        "pem_private_key": pem,
        "sfRole": c.get("snowflake.role"),
        "sfWarehouse": c.get("snowflake.warehouse"),
        "sfDatabase": c.get("snowflake.database"),
        "sfSchema": schema or c.get("snowflake.schema"),
        "column_mapping": "name",
        "truncate_table": "on",  # overwrite keeps the stage table's DDL instead of recreating it
    }


def run_snowflake_sql(spark: SparkSession, opts: Dict[str, str], sql: str) -> None:
    spark.sparkContext._jvm.net.snowflake.spark.snowflake.Utils.runQuery(opts, sql)


def read_snowflake(spark: SparkSession, opts: Dict[str, str], query: str) -> DataFrame:
    return spark.read.format("snowflake").options(**opts).option("query", query).load()


def gold_changes(spark: SparkSession, ctx: RunContext, spec: PublishSpec):
    table = ctx.config.fq("gold", spec.table)
    latest = latest_table_version(spark, table)
    last = get_version_watermark(spark, ctx, CONSUMER, table)
    if last is None:
        return spark.table(table).withColumn("_change_type", F.lit("insert")), latest, True
    if last >= latest:
        return None, latest, False
    cdf = spark.read.format("delta").option("readChangeFeed", "true").option("startingVersion", last + 1).option("endingVersion", latest).table(table)
    cdf = cdf.filter("_change_type != 'update_preimage'")
    w = Window.partitionBy(*spec.keys).orderBy(F.col("_commit_version").desc(), F.col("_change_type").desc())
    latest_per_key = cdf.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn", "_commit_version", "_commit_timestamp")
    return latest_per_key, latest, False


def build_snowflake_merge(target: str, stage: str, keys: List[str], columns: List[str]) -> str:
    """Pure SQL builder (unit tested). Upper-cases identifiers to match Snowflake's default."""
    up = [c.upper() for c in columns]
    on = " AND ".join(f"t.{k.upper()} = s.{k.upper()}" for k in keys)
    return f"""MERGE INTO {target} t
USING {stage} s
ON {on}
WHEN MATCHED AND s._CHANGE_TYPE = 'delete' THEN DELETE
WHEN MATCHED THEN UPDATE SET {", ".join(f"t.{c} = s.{c}" for c in up)}
WHEN NOT MATCHED AND s._CHANGE_TYPE <> 'delete' THEN INSERT ({", ".join(up)}) VALUES ({", ".join("s." + c for c in up)})"""


def publish_table(spark: SparkSession, ctx: RunContext, spec: PublishSpec) -> Optional[recon.ReconResult]:
    cfg = ctx.config
    changes, version, is_full = gold_changes(spark, ctx, spec)
    table = cfg.fq("gold", spec.table)
    if changes is None:
        return None

    opts_gold = snowflake_options(ctx)
    opts_stage = snowflake_options(ctx, cfg.get("snowflake.stage_schema"))
    stage_name = f"{spec.table.upper()}_CHANGES"
    target_name = f"{cfg.get('snowflake.schema')}.{spec.table.upper()}"
    data_cols = [c for c in changes.columns if c != "_change_type"]

    changes.select(*data_cols, F.col("_change_type").alias("_CHANGE_TYPE")).write.format("snowflake").options(**opts_stage).option("dbtable", stage_name).mode(
        "overwrite"
    ).save()
    if is_full:  # first publish / reset: make Snowflake an exact replica
        run_snowflake_sql(spark, opts_gold, f"TRUNCATE TABLE IF EXISTS {target_name}")
    run_snowflake_sql(spark, opts_gold, build_snowflake_merge(target_name, f"{cfg.get('snowflake.stage_schema')}.{stage_name}", spec.keys, data_cols))

    # GOLD_TO_SNOWFLAKE reconciliation on the FULL table (cheap: metadata-assisted counts)
    measure = spec.measure
    g = spark.table(table).agg(F.count(F.lit(1)).alias("c"), (F.sum(measure) if measure else F.lit(0)).alias("m")).collect()[0]
    sf_sql = f"SELECT COUNT(*) AS C, {'SUM(' + measure.upper() + ')' if measure else '0'} AS M FROM {target_name}"
    s = read_snowflake(spark, opts_gold, sf_sql).collect()[0]
    result = recon.compare_counts_and_amounts(
        "GOLD_TO_SNOWFLAKE",
        spec.table,
        g["c"],
        s["C"],
        float(g["m"] or 0),
        float(s["M"] or 0),
        amount_tolerance_pct=float(cfg.get("reconciliation.amount_tolerance_pct")),
        detail=f"gold_version={version} mode={'FULL' if is_full else 'CDF'}",
    )
    if result.status == recon.PASSED:
        advance_version_watermark(spark, ctx, CONSUMER, table, version)
    return result


def publish_all(spark: SparkSession, ctx: RunContext) -> List[recon.ReconResult]:
    results = []
    for spec in PUBLISH_SPECS:  # dimensions first, so facts never reference a missing SK in Snowflake
        r = publish_table(spark, ctx, spec)
        if r:
            results.append(r)
    recon.persist_results(spark, ctx, results)
    recon.assert_all_passed(results)
    return results

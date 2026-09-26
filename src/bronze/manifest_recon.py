"""
ADF -> Bronze reconciliation.

ADF writes raw/_manifests/<entity>/<run_id>.json after ITS OWN count validation
(source_count == rows_read == rows_copied). Here Databricks proves that every
row ADF copied actually arrived in bronze:

    manifest.rows_copied  ==  COUNT(*) FROM bronze.<entity> WHERE _ingestion_run_id = run_id

Only manifests not yet PASSED are checked, so the check is incremental and a
previously failed run_id is re-checked on every run until it is fixed.
"""

from __future__ import annotations

from typing import List

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from src.common.config import EntityConfig
from src.framework.reconciliation import ReconResult, compare_counts
from src.framework.run_context import RunContext


def reconcile_manifests(spark: SparkSession, ctx: RunContext, entity: EntityConfig) -> List[ReconResult]:
    cfg = ctx.config
    manifest_path = f"{cfg.get('storage.manifest_root')}/{entity.name}/*.json"
    try:
        manifests = spark.read.option("multiLine", "true").json(manifest_path)
    except Exception:  # no manifests yet for a brand-new entity
        return []

    already_passed = spark.sql(
        f"SELECT DISTINCT regexp_extract(detail, 'run_id=([^ ]+)', 1) AS run_id FROM {cfg.fq('control', 'reconciliation_results')} "
        f"WHERE recon_type = 'ADF_TO_BRONZE' AND entity_name = '{entity.name}' AND status = 'PASSED'"
    )
    pending = manifests.join(already_passed, "run_id", "left_anti").select("run_id", "rows_copied")

    bronze_counts = spark.table(cfg.fq("bronze", entity.name)).groupBy(F.col("_ingestion_run_id").alias("run_id")).agg(F.count(F.lit(1)).alias("bronze_count"))

    results = []
    tolerance = int(cfg.get("reconciliation.count_tolerance", 0))
    for row in pending.join(bronze_counts, "run_id", "left").collect():
        results.append(compare_counts("ADF_TO_BRONZE", entity.name, row["rows_copied"], row["bronze_count"] or 0, tolerance, detail=f"run_id={row['run_id']}"))
    return results

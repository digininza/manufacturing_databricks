"""
Generic Auto Loader ingestion: raw parquet (written by ADF) -> bronze Delta.

ONE function serves every batch entity in configs/source_registry.yml.

Why Auto Loader instead of spark.read on the folder:
  * it tracks which files were already ingested in the checkpoint (RocksDB), so
    re-running the job never double-loads a file -> idempotent by design;
  * `trigger(availableNow=True)` gives batch-job economics with streaming
    bookkeeping: the cluster processes everything new, then stops;
  * schema evolution: a column added in the source is added to bronze
    (`addNewColumns`) instead of breaking the load; unparseable values land in
    `_rescued_data` instead of being silently dropped.

Bronze is APPEND-ONLY and keeps the source columns as delivered, plus lineage
columns. No business logic here — bronze must always be replayable.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from src.common.config import Config, EntityConfig


def read_raw_stream(spark: SparkSession, config: Config, entity: EntityConfig) -> DataFrame:
    source_path = f"{config.get('storage.raw_root')}/{entity.raw_path}/"
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .option("cloudFiles.schemaLocation", f"{config.get('storage.schema_root')}/{entity.name}")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("cloudFiles.includeExistingFiles", "true")
        .option("cloudFiles.maxFilesPerTrigger", 500)
        .option("rescuedDataColumn", "_rescued_data")
        .option("pathGlobFilter", "*.parquet")
        .load(source_path)
    )


def add_lineage_columns(df: DataFrame, entity: EntityConfig) -> DataFrame:
    """Lineage columns that link every bronze row back to the ADF run that landed it."""
    return (
        df.withColumn("_source_file", F.col("_metadata.file_path"))
        .withColumn("_source_file_ts", F.col("_metadata.file_modification_time"))
        .withColumn("_ingestion_run_id", F.regexp_extract(F.col("_metadata.file_path"), r"run_id=([^/]+)", 1))
        .withColumn("_load_date", F.to_date(F.regexp_extract(F.col("_metadata.file_path"), r"load_date=(\d{4}-\d{2}-\d{2})", 1)))
        .withColumn("_source_system", F.lit(entity.source_system))
        .withColumn("_ingest_ts", F.current_timestamp())
    )


def ingest_entity_to_bronze(spark: SparkSession, config: Config, entity: EntityConfig) -> None:
    target = config.fq("bronze", entity.name)
    query = (
        add_lineage_columns(read_raw_stream(spark, config, entity), entity)
        .writeStream.format("delta")
        .option("checkpointLocation", config.checkpoint("bronze", entity.name))
        .option("mergeSchema", "true")
        .trigger(availableNow=True)
        .queryName(f"bronze_{entity.name}")
        .toTable(target)
    )
    query.awaitTermination()

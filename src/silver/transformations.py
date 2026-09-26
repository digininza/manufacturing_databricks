"""
Cleansing + standardization + null handling, driven entirely by source_registry.yml.

Order of operations per column:
  1. string transforms (trim / upper / lower / initcap / empty_to_null)
  2. cast to the target type (a value that cannot be cast becomes NULL, and the
     DQ rules then catch it -> quarantine, never a silent wrong value)
  3. rename to the conformed silver name (Oracle UPPER_CASE -> snake_case)
Then null_defaults fill business defaults (e.g. units_scrapped NULL -> 0).
"""

from __future__ import annotations

from typing import List

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from src.common.config import EntityConfig

# Lineage / CDC columns carried from bronze untouched.
PASSTHROUGH_PREFIXES = ("cdc_", "_ingestion_run_id", "_source_file", "_source_system", "_ingest_ts", "_load_date")


def apply_transforms(col: Column, transforms: List[str]) -> Column:
    for t in transforms:
        if t == "trim":
            col = F.trim(col)
        elif t == "upper":
            col = F.upper(col)
        elif t == "lower":
            col = F.lower(col)
        elif t == "initcap":
            col = F.initcap(col)
        elif t == "empty_to_null":
            col = F.when(F.trim(col) == "", F.lit(None)).otherwise(col)
    return col


def standardize(df: DataFrame, entity: EntityConfig) -> DataFrame:
    selected = []
    for source_col, spec in entity.columns.items():
        if source_col in df.columns:
            c = F.col(f"`{source_col}`")
            if spec.get("transforms"):
                c = apply_transforms(c.cast("string"), spec["transforms"])
        else:  # column not (yet) delivered by the source: keep the contract, fill NULL
            c = F.lit(None)
        selected.append(c.cast(spec["type"]).alias(spec["name"]))
    selected += [F.col(c) for c in df.columns if c.startswith(PASSTHROUGH_PREFIXES)]
    out = df.select(*selected)

    for col_name, default in entity.null_defaults.items():
        out = out.withColumn(col_name, F.coalesce(F.col(col_name), F.lit(default).cast(out.schema[col_name].dataType)))
    return out


def sequence_column(df: DataFrame, entity: EntityConfig) -> Column:
    """A single sortable string per row version: 'latest wins' is max(_sequence).
    CDC: start_lsn|seqval (fixed-width hex). Timestamps: zero-padded ISO text."""
    parts = []
    for c in entity.sequence_by:
        dtype = df.schema[c].dataType.simpleString()
        parts.append(F.date_format(F.col(c), "yyyy-MM-dd HH:mm:ss.SSSSSS") if dtype == "timestamp" else F.col(c).cast("string"))
    return F.concat_ws("|", *parts)


def add_silver_metadata(df: DataFrame, entity: EntityConfig, pipeline_run_id: str) -> DataFrame:
    business_cols = [spec["name"] for spec in entity.columns.values()]
    out = df.withColumn("_sequence", sequence_column(df, entity))
    out = out.withColumn("_is_deleted", F.expr(entity.delete_condition) if entity.delete_condition else F.lit(False))
    out = out.withColumn("_row_hash", F.sha2(F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("~")) for c in business_cols]), 256))
    return out.withColumn("_pipeline_run_id", F.lit(pipeline_run_id)).withColumn("_silver_updated_ts", F.current_timestamp())

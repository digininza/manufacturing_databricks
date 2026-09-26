"""
CDC / upsert application into silver.

Two problems every incremental batch has, solved here:

1. The same key can appear MANY times in one batch
   (insert then 2 updates, or ADF retried and the same change landed twice).
   -> keep only the latest version per key (max _sequence). Exact duplicates are
      dropped first. Both are counted so reconciliation still balances.

2. Batches can be replayed (job retry, checkpoint rollback, backfill).
   -> MERGE only lets a row overwrite silver if its _sequence is NEWER than
      what silver already holds. Replaying an old batch is therefore a no-op.

Deletes are SOFT in silver (`_is_deleted = true`): the row stays for audit, the
Change Data Feed emits the change, and gold recomputes the affected dates.
"""

from __future__ import annotations

from typing import List, Tuple

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F


def deduplicate_latest(df: DataFrame, keys: List[str]) -> Tuple[DataFrame, int]:
    """Returns (one row per key, number of rows removed)."""
    before = df.count()
    exact = df.dropDuplicates([c for c in df.columns if c not in ("_ingest_ts", "_source_file", "_source_file_ts", "_silver_updated_ts")])
    w = Window.partitionBy(*keys).orderBy(F.col("_sequence").desc())
    latest = exact.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")
    after = latest.count()
    return latest, before - after


def build_merge_sql(target: str, source_view: str, keys: List[str], columns: List[str]) -> str:
    """Idempotent, out-of-order-safe CDC merge. Pure string building -> unit tested."""
    on = " AND ".join(f"t.`{k}` <=> s.`{k}`" for k in keys)
    set_clause = ",\n        ".join(f"t.`{c}` = s.`{c}`" for c in columns)
    insert_cols = ", ".join(f"`{c}`" for c in columns)
    insert_vals = ", ".join(f"s.`{c}`" for c in columns)
    return f"""MERGE INTO {target} t
USING {source_view} s
ON {on}
WHEN MATCHED AND s._sequence > t._sequence THEN UPDATE SET
        {set_clause}
WHEN NOT MATCHED AND s._is_deleted = false THEN INSERT ({insert_cols})
    VALUES ({insert_vals})"""

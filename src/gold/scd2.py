"""
Generic SCD Type 2 MERGE (single atomic statement).

Pattern ("staged union"):
  staged = every source row keyed by its business key (merge_key = key)
           UNION ALL
           source rows whose tracked attributes CHANGED vs the current version,
           with merge_key = NULL (so they can never match -> they INSERT the new version)

  MERGE target t USING staged s ON t.key = s.merge_key AND t.is_current
    WHEN MATCHED AND hash differs  -> close the current version (is_current=false, effective_to=change ts)
    WHEN NOT MATCHED               -> insert (brand-new key OR the new version of a changed key)

Surrogate key = xxhash64(business key, effective_from): DETERMINISTIC. Rebuilding
gold from silver (backfill, DR, new environment) regenerates the SAME keys, so
facts and Snowflake/Power BI never get orphaned. An IDENTITY column would give
different numbers on every rebuild. -1 is reserved for the "Unknown" member.

First version of a key gets effective_from = 1900-01-01 so facts that happened
before we first saw the dimension row still resolve to a real member.
Re-running with unchanged source = no-op (hash equal) -> idempotent.
"""

from __future__ import annotations

from typing import List

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

HIGH_DATE = "9999-12-31 00:00:00"
LOW_DATE = "1900-01-01 00:00:00"


def attr_hash(cols: List[str]):
    return F.sha2(F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("~")) for c in cols]), 256)


def surrogate_key(business_keys: List[str], effective_col: str = "effective_from"):
    return F.xxhash64(*[F.col(k).cast("string") for k in business_keys], F.col(effective_col).cast("string"))


def scd2_merge(
    spark: SparkSession,
    source: DataFrame,
    target: str,
    business_keys: List[str],
    tracked_cols: List[str],
    change_ts_col: str,
    sk_col: str,
    run_id: str,
) -> None:
    src = source.withColumn("attr_hash", attr_hash(tracked_cols))
    current = spark.table(target).filter("is_current = true AND " + f"{sk_col} <> -1").select(*business_keys, F.col("attr_hash").alias("_cur_hash"))

    known = src.join(current, business_keys, "left")
    # brand-new keys get LOW_DATE; changed keys start at the source change timestamp
    known = known.withColumn(
        "effective_from", F.when(F.col("_cur_hash").isNull(), F.to_timestamp(F.lit(LOW_DATE))).otherwise(F.col(change_ts_col).cast("timestamp"))
    )
    changed = known.filter(F.col("_cur_hash").isNotNull() & (F.col("_cur_hash") != F.col("attr_hash")))

    key_expr = F.concat_ws("||", *[F.col(k).cast("string") for k in business_keys])
    staged = known.withColumn("merge_key", key_expr).unionByName(changed.withColumn("merge_key", F.lit(None).cast("string")))
    staged = staged.withColumn(sk_col, surrogate_key(business_keys)).drop("_cur_hash")
    staged.createOrReplaceTempView("_scd2_staged")

    target_key = "concat_ws('||', " + ", ".join(f"cast(t.{k} as string)" for k in business_keys) + ")"
    cols = [sk_col] + business_keys + tracked_cols + ["attr_hash", "effective_from"]
    spark.sql(
        f"""
        MERGE INTO {target} t
        USING _scd2_staged s
        ON {target_key} = s.merge_key AND t.is_current = true
        WHEN MATCHED AND t.attr_hash <> s.attr_hash THEN UPDATE SET
            t.is_current = false,
            t.effective_to = s.effective_from,
            t._pipeline_run_id = '{run_id}',
            t._gold_updated_ts = current_timestamp()
        WHEN NOT MATCHED THEN INSERT ({", ".join(cols)}, effective_to, is_current, _pipeline_run_id, _gold_updated_ts)
            VALUES ({", ".join("s." + c for c in cols)}, TIMESTAMP'{HIGH_DATE}', true, '{run_id}', current_timestamp())
        """
    )


def point_in_time_join(fact: DataFrame, dim: DataFrame, business_key: str, event_ts_col: str, sk_col: str, unknown_sk: int = -1) -> DataFrame:
    """Attach the dimension version that was valid WHEN the event happened (not today's version)."""
    d = dim.filter(f"{sk_col} <> -1").select(
        F.col(business_key).alias("_d_key"),
        F.col(sk_col).alias(f"_{sk_col}"),
        F.col("effective_from").alias("_eff_from"),
        F.col("effective_to").alias("_eff_to"),
    )
    cond = (fact[business_key] == d["_d_key"]) & (fact[event_ts_col] >= d["_eff_from"]) & (fact[event_ts_col] < d["_eff_to"])
    return (
        # BROADCAST the dimension: a few thousand SCD2 versions (~MBs) are copied to every executor,
        # so the multi-billion-row fact is joined IN PLACE — no shuffle of the fact, therefore no skew
        # even if one machine owns 30% of the rows. It matters doubly because this is a RANGE
        # (non-equi) join on effective_from/to: without the hint Spark may choose a sort-merge join on
        # business_key only, and a hot key then lands in a single straggler task.
        fact.join(F.broadcast(d), cond, "left")
        .withColumn(sk_col, F.coalesce(F.col(f"_{sk_col}"), F.lit(unknown_sk).cast("bigint")))
        .drop("_d_key", f"_{sk_col}", "_eff_from", "_eff_to")
    )

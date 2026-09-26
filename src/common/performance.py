"""
Performance toolkit for TB-scale processing: shuffle tuning, skew handling, salting, repartition/coalesce.

WHERE EACH TECHNIQUE IS USED: see docs/PERFORMANCE_AND_SCALE.md (file-by-file index).

Mental model — every expensive Spark step is a SHUFFLE (groupBy, join, window, distinct, MERGE).
A shuffle is only as fast as its SLOWEST partition, so the two enemies are:
  1. wrong partition COUNT  -> too few = huge partitions spilling to disk; too many = scheduler
                               overhead + thousands of tiny output files;
  2. SKEW                   -> one key (e.g. one very busy machine, or the NULL / 'UNKNOWN' key)
                               owns a huge share of rows, so one task runs for an hour while
                               199 others finish in seconds.

Order of defence (cheapest first):
  a. AQE (adaptive query execution): coalesces small shuffle partitions and SPLITS skewed join
     partitions automatically at runtime                       -> apply_batch_tuning()
  b. broadcast the small side of a join: no shuffle at all     -> F.broadcast(dim) in src/gold/
  c. repartition by the key the NEXT operations need, so several operations share ONE shuffle
                                                               -> src/silver/cdc.py
  d. SALTING when neither side of a skewed join can be broadcast and AQE's skew split does not
     kick in (range joins, MERGE sources, streaming micro-batches)       -> salted_join()
     used in src/gold/process_sensor_stats.py (sensor readings x production runs, backfill mode)

Note on skewed AGGREGATIONS: for sum/count/min/max Spark already does a map-side PARTIAL
aggregation (HashAggregate partial -> shuffle -> final), so a hot key ships at most one partial
row per map task — skew rarely hurts there. It DOES hurt for aggregations with no partial form
(collect_list, exact percentiles) and for WINDOW functions partitioned by a hot key; the fix there
is the same salting idea (add a salt to the partition key, then combine).
"""

from __future__ import annotations

from typing import List, Tuple

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

# ----------------------------------------------------------------------------- session-level tuning


def apply_batch_tuning(spark: SparkSession) -> None:
    """
    Settings for the BATCH job (also set as cluster spark_conf in resources/jobs/mfg_batch_pipeline.yml;
    repeated here so interactive runs behave like the job).

    * adaptive.enabled                 AQE re-plans each stage using REAL shuffle statistics.
    * coalescePartitions               merges tiny post-shuffle partitions (avoids 200 x 1 MB files).
    * advisoryPartitionSizeInBytes     target ~128 MB per shuffle partition.
    * skewJoin.enabled                 a join partition > 5x the median AND > 256 MB is split into
                                       sub-partitions, the other side's matching partition replicated.
    * autoBroadcastJoinThreshold       tables below 64 MB are broadcast (all our dimensions).
    * shuffle.partitions = auto        Databricks picks the initial count from input size; AQE then
                                       coalesces. (On OSS Spark set a number: ~ input GB * 8.)
    """
    conf = {
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
        "spark.sql.adaptive.advisoryPartitionSizeInBytes": "128m",
        "spark.sql.adaptive.skewJoin.enabled": "true",
        "spark.sql.adaptive.skewJoin.skewedPartitionFactor": "5",
        "spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes": "256m",
        "spark.sql.autoBroadcastJoinThreshold": "64m",
    }
    for k, v in conf.items():
        spark.conf.set(k, v)
    try:  # Databricks-only setting; ignored on OSS Spark
        spark.conf.set("spark.sql.shuffle.partitions", "auto")
    except Exception:
        spark.conf.set("spark.sql.shuffle.partitions", "400")


# ----------------------------------------------------------------------------- diagnostics


def key_skew_report(df: DataFrame, key_cols: List[str], top_n: int = 10) -> Tuple[List[dict], float]:
    """
    How skewed is `key_cols`? Returns the heaviest keys and the ratio max/median rows-per-key.
    Rule of thumb: ratio > 10 on a key you shuffle by => expect a straggler task; consider salting.
    (Use it once on a sample when designing a job, not on every run — it is itself a shuffle.)
    """
    counts = df.groupBy(*key_cols).count()
    top = [r.asDict() for r in counts.orderBy(F.col("count").desc()).limit(top_n).collect()]
    median = counts.approxQuantile("count", [0.5], 0.01)[0] or 1
    ratio = (top[0]["count"] / median) if top else 0.0
    return top, ratio


# ----------------------------------------------------------------------------- salting


def salted_join(big: DataFrame, medium: DataFrame, key: str, salt_buckets: int = 16, how: str = "inner") -> DataFrame:
    """
    Skewed join where the other side is TOO BIG TO BROADCAST (else just use F.broadcast()).

      big side:    random salt 0..N-1 per row          -> hot key spread over N partitions
      medium side: every row EXPLODED N times (salt 0..N-1) -> each salted partition still finds its match
    Cost: the medium side grows N times, so keep N as small as removes the straggler (8-32).
    Try AQE skewJoin first; salt manually when AQE's thresholds don't trigger or the plan can't
    be changed (e.g. inside a MERGE source, or on a streaming micro-batch).
    """
    big_s = big.withColumn("_salt", (F.rand(seed=7) * salt_buckets).cast("int"))
    med_s = medium.withColumn("_salt", F.explode(F.sequence(F.lit(0), F.lit(salt_buckets - 1))))
    return big_s.join(med_s, [key, "_salt"], how).drop("_salt")


# ----------------------------------------------------------------------------- output file sizing


def files_for(rows: int, rows_per_file: int = 2_000_000, max_files: int = 64) -> int:
    """How many output files for `rows` rows (~128-256 MB parquet each at typical row widths).
    Used with COALESCE (no shuffle: merges existing partitions) when shrinking, e.g. before
    writing a change set to Snowflake. Use REPARTITION (full shuffle, even sizes) only when you
    must INCREASE parallelism or re-distribute by a key."""
    return max(1, min(max_files, -(-rows // rows_per_file)))

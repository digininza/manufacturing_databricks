"""Silver logic on real Spark (no Delta needed): standardize -> DQ -> dedup -> balance.
Skipped automatically when pyspark/JDK are missing; runs in CI."""

import pytest

from tests.conftest import requires_spark

pytestmark = requires_spark


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("northforge-tests")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def bronze_df(spark, rows):
    """Same shape the landing writes: every source column as STRING + bronze lineage columns."""
    from pyspark.sql import functions as F

    cols = list(dict.fromkeys(k for r in rows for k in r))
    df = spark.createDataFrame([tuple(None if r.get(c) is None else str(r[c]) for c in cols) for r in rows], ", ".join(f"`{c}` STRING" for c in cols))
    return df.withColumn("_ingestion_run_id", F.lit("test_run")).withColumn("_source_file", F.lit("f.parquet")).withColumn("_ingest_ts", F.current_timestamp())


def silver_pipeline(spark, rows):
    from src.common.config import load_registry
    from src.framework.dq import apply_dq
    from src.silver.cdc import deduplicate_latest
    from src.silver.transformations import add_silver_metadata, standardize

    entity = load_registry()["mes_production_log"]
    std = add_silver_metadata(standardize(bronze_df(spark, rows), entity), entity, "test")
    valid, quarantined, stats = apply_dq(std, entity.dq_rules)
    latest, dups = deduplicate_latest(valid, entity.primary_keys)
    return latest, quarantined, dups, stats


def test_day1_cleansing_quarantine_and_dedup(spark, generated):
    from src.framework.reconciliation import PASSED, check_silver_balance

    _, batches = generated
    rows = batches["mes_production_log"][0].rows
    latest, quarantined, dups, stats = silver_pipeline(spark, rows)

    assert quarantined.count() == 2  # negative units + scrap > produced
    assert stats["non_negative_units"] == 1 and stats["scrap_not_above_produced"] == 1
    assert dups == 1  # the change ADF landed twice
    assert check_silver_balance("mes_production_log", len(rows), latest.count(), 2, dups).status == PASSED

    by_id = {r["production_log_id"]: r for r in latest.collect()}
    assert by_id[100004]["machine_id"] == "M-P01-04"  # ' m-p01-04 ' trimmed + upper-cased
    assert by_id[100006]["shift_code"] == "UNKNOWN"  # NULL -> business default (null_defaults)


def test_day2_latest_version_wins_and_deletes_flagged(spark, generated):
    _, batches = generated
    day1 = {r["production_log_id"]: r for r in batches["mes_production_log"][0].rows}
    latest, quarantined, dups, _ = silver_pipeline(spark, batches["mes_production_log"][1].rows)

    assert quarantined.count() == 0
    assert dups == 1  # 100001 was updated twice in the window -> older version collapsed
    by_id = {r["production_log_id"]: r for r in latest.collect()}
    assert by_id[100001]["units_scrapped"] == day1[100001]["units_scrapped"] + 3
    assert by_id[100002]["_is_deleted"] and by_id[100003]["_is_deleted"]
    assert not by_id[100009]["_is_deleted"] and by_id[100009]["units_scrapped"] == 4


def test_salted_join_returns_same_rows_as_plain_join(spark):
    """Salting must change the PLAN, never the RESULT."""
    from src.common.performance import salted_join

    big = spark.createDataFrame([("HOT", i) for i in range(500)] + [("COLD", i) for i in range(10)], "machine_id STRING, reading INT")
    runs = spark.createDataFrame([("HOT", "run1"), ("COLD", "run2")], "machine_id STRING, run STRING")
    plain = sorted(big.join(runs, "machine_id").collect())
    salted = sorted(salted_join(big, runs, "machine_id", salt_buckets=8).select(*big.columns, "run").collect())
    assert plain == salted

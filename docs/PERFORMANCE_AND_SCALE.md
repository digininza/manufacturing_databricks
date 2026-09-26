# Performance and scale: partitioning, skew, salting, OPTIMIZE / VACUUM / Z-ORDER

Every technique below is in the code with a comment explaining **which key and why**.
Use this page as the index. The shared helpers are in [src/common/performance.py](../src/common/performance.py).

## 1. Where each technique is used

| Technique | File (line) | Key / setting | Why here |
|---|---|---|---|
| **AQE** (adaptive query execution), including skew-join split, partition coalescing and the broadcast threshold | [performance.py:41](../src/common/performance.py) `apply_batch_tuning`; job cluster conf [mfg_batch_pipeline.yml:50](../resources/jobs/mfg_batch_pipeline.yml); called in [processor.py:152](../src/silver/processor.py) and [facts.py:381](../src/gold/facts.py) | 128 MB target partitions; a partition is skewed if > 5× the median and > 256 MB; broadcast below 64 MB; `shuffle.partitions = auto` | The first, free defence. It re-plans every stage using real shuffle sizes. |
| **repartition by key** | [cdc.py:39](../src/silver/cdc.py) | **Primary key** (e.g. `production_log_id`) | `dropDuplicates` and the latest-per-key window both need a key's versions together. One hash-repartition serves both, so there is **one shuffle instead of two**. The primary key is high-cardinality, so there is no skew. |
| **coalesce** (shrink with no shuffle) | [snowflake_publisher.py:133](../src/publish/snowflake_publisher.py) | `files_for(rows)`, max 32 | The connector uploads one file per partition. It turns hundreds of tiny partitions into a few right-sized files **without** a shuffle. |
| coalesce(1) (the anti-pattern, explained) | [01_simulate_adf_landing.py:39](../notebooks/00_setup/01_simulate_adf_landing.py) | n/a | Fine for a demo of a few hundred rows. The comment explains why never to do it at volume. |
| **broadcast join** | [scd2.py:101](../src/gold/scd2.py) (point-in-time SCD2 lookup), [facts.py:158-160](../src/gold/facts.py) (product, machine, plant), [process_sensor_stats.py:72](../src/gold/process_sensor_stats.py) (runs, incremental mode) | Small side = the dimension, or one day's runs | No shuffle of the big fact, so skew is **impossible**. This also covers the `-1`/`UNKNOWN` hot key of late-arriving rows. |
| **Salting a skewed join** | [process_sensor_stats.py:74](../src/gold/process_sensor_stats.py), helper [performance.py:91](../src/common/performance.py) `salted_join` | `machine_id` + salt 0..31 | Sensor readings (billions of rows) × production runs: only ~40 machine keys, and presses emit 10× more readings. In backfill mode the runs are too big to broadcast, so salting spreads each hot machine over 32 tasks. Tested for identical results in `tests/test_spark_silver.py`. |
| Skew diagnosis | [performance.py:75](../src/common/performance.py) `key_skew_report` | Any key | Top keys and the max/median ratio. A ratio above 10 on a shuffle key means you should expect a straggler. |
| **Aggregation skew: why no salt** | [facts.py:123](../src/gold/facts.py) | `(date, machine, product)` | Sum and count aggregate partially on the map side first, so a hot key sends one partial row per task. |
| **Hive partitionBy** (the only places it is used) | [eventhub_to_bronze.py:66](../src/streaming/eventhub_to_bronze.py) `_ingest_date`; [sensor_silver.py:86](../src/streaming/sensor_silver.py) `event_date` | Date | Append-only, low cardinality, **15-25 GB per day** per partition, and always filtered by date. |
| **Partition pruning** | [process_sensor_stats.py:67](../src/gold/process_sensor_stats.py) | `event_date IN (affected days, +1)` | Reads 2 date folders out of years of IoT data. |
| **File pruning inside MERGE** | [facts.py:100](../src/gold/facts.py) | `t.production_date IN (affected dates)` in the ON clause | Without it, MERGE scans the whole multi-year fact to update 3 days. This is the #1 cause of slow MERGEs. |
| **Liquid clustering** (`CLUSTER BY`) | Gold: [02_gold_tables.sql](../sql/databricks/02_gold_tables.sql), explained at line 79. Silver: [processor.py:63](../src/silver/processor.py). Control: `01_control_tables.sql` | Gold: `(production_date, machine_id)`. Silver: the **primary key**. | Data skipping for date-driven MERGEs and queries, and key-range pruning for CDC MERGEs. There are no small-partition problems, and the keys can be changed later. |
| **Z-ORDER** | [table_maintenance.py:65](../src/framework/table_maintenance.py) | `silver.iot_sensor_reading`: `ZORDER BY (machine_id, sensor_type)`, last 3 partitions only | Z-ORDER is used for the Hive-partitioned IoT tables. It is not allowed on liquid-clustered tables. |
| **OPTIMIZE** | [table_maintenance.py:109](../src/framework/table_maintenance.py), weekly job `resources/jobs/mfg_table_maintenance.yml`, notebook `notebooks/08_maintenance/optimize_vacuum.py` | Per-table strategy list | Compacts small files from micro-batches and MERGEs, re-clusters, and purges rows soft-deleted by deletion vectors. |
| **VACUUM** | [table_maintenance.py:43](../src/framework/table_maintenance.py) | `RETAIN 168 HOURS` | Removes old files (storage cost). The retention covers time travel and the Change Data Feed consumers, and a test enforces ≥ 7 days. |
| **ANALYZE TABLE** (statistics) | `table_maintenance.py` (gold facts and dimensions) | All columns | Feeds the optimizer's join-order and broadcast decisions. |
| **optimizeWrite / autoCompact** | [processor.py:55](../src/silver/processor.py) table properties; batch cluster conf | n/a | Right-sized files at write time, so there are fewer small files for OPTIMIZE to fix. |
| **Deletion vectors** | [processor.py:55](../src/silver/processor.py) | n/a | A CDC MERGE that touches 10k rows no longer rewrites GBs of parquet. |
| **Micro-batch size limits** | [processor.py:158](../src/silver/processor.py) `maxFilesPerTrigger=200`; Auto Loader `maxFilesPerTrigger=500`; Event Hub `maxOffsetsPerTrigger` | n/a | A TB backfill runs as bounded batches instead of one giant batch that spills and restarts from zero. |
| **Streaming shuffle partitions (fixed)** | [sensor_silver.py:63](../src/streaming/sensor_silver.py) | 64 | State is partitioned by this number and frozen in the checkpoint. AQE does not apply to stateful operators. |

## 2. Rules of thumb used when choosing keys

| Question | Rule | Applied as |
|---|---|---|
| Partition a table with `partitionBy`? | Only if it has **low cardinality**, each partition is **≥ 1 GB**, the table is append-mostly, and queries **always filter** on the column | Yes for the IoT tables (by date). No for silver, gold and batch bronze. |
| Otherwise? | **Liquid clustering** on the columns you filter or MERGE by | Gold `(production_date, machine_id)`; silver on the primary key |
| Which key to repartition by before a wide operation? | The key the **next** operations group, join or window by. It must be **high-cardinality** and evenly spread. | Silver dedup: the primary key. Never `machine_id` (40 values). |
| Increase or decrease the number of partitions? | Decrease → `coalesce(n)` (narrow, no shuffle). Increase or redistribute → `repartition(n, key)` (full shuffle). | Snowflake upload uses coalesce; silver dedup uses repartition. |
| Join skewed? | 1) Broadcast the small side; 2) AQE `skewJoin`; 3) **salt** | Dimensions are broadcast. Sensor × runs is salted in backfill mode. |
| Aggregation skewed? | Sum/count/min/max are mostly fine (partial aggregation). Salt collect-style aggregates and window functions partitioned by a hot key. | `fact_production_daily` needs no salt. |
| Hot NULL / `UNKNOWN` key? | Filter it or handle it before the join, or broadcast the other side | Unknown members (`-1`) are only ever joined to broadcast dimensions |

## 3. How to confirm a skew problem on Databricks

1. **Spark UI → Stages** for the slow job. Look at the task duration summary: if **Max ≫ 75th percentile** (for example 40 minutes against 30 seconds), that is skew.
2. The **SQL/DataFrame tab** shows AQE annotations: `CustomShuffleReader coalesced` or `skewed` partitions.
3. Run `key_skew_report(df, ["machine_id"])` on the join or group key to find the heavy keys.
4. Fix it with broadcast, then AQE, then salting, and check that the stage's max task time drops.

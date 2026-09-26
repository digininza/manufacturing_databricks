"""
IoT telemetry: Azure Event Hub -> bronze.iot_sensor_raw  (continuous Structured Streaming)

Machines -> edge gateway (OPC-UA/MQTT) -> Azure IoT Hub / Event Hub `machine-telemetry`
(32 partitions, 7-day retention, consumer group `databricks-bronze`).

We read Event Hub through its KAFKA-compatible endpoint (port 9093). That needs
no extra library on the cluster (the Kafka source is built into Databricks
Runtime) and is the pattern Microsoft recommends for Spark.

Bronze stores the message EXACTLY as received (body as string + Event Hub
partition/offset/enqueued time). Parsing happens in silver, so a bad deploy of
the parser can always be fixed by replaying bronze — the raw evidence is kept.

Exactly-once into bronze: the checkpoint stores Kafka offsets and the Delta sink
is idempotent per micro-batch, so a cluster restart resumes where it stopped
without loss or duplication. Event Hub retention (7 days) is the recovery window.
"""

from __future__ import annotations

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from src.common.config import Config
from src.common.notebook_utils import get_secret


def eventhub_kafka_options(config: Config) -> dict:
    conn = get_secret(config.get("eventhub.secret_scope"), config.get("eventhub.secret_key"))  # Listen-only SAS policy
    namespace = config.get("eventhub.namespace")
    jaas = f'kafkashaded.org.apache.kafka.common.security.plain.PlainLoginModule required username="$ConnectionString" password="{conn}";'
    return {
        "kafka.bootstrap.servers": f"{namespace}.servicebus.windows.net:9093",
        "subscribe": config.get("eventhub.hub_name"),
        "kafka.group.id": config.get("eventhub.consumer_group"),
        "kafka.security.protocol": "SASL_SSL",
        "kafka.sasl.mechanism": "PLAIN",
        "kafka.sasl.jaas.config": jaas,
        "kafka.request.timeout.ms": "60000",
        "kafka.session.timeout.ms": "30000",
        "startingOffsets": config.get("eventhub.starting_offsets", "latest"),
        "maxOffsetsPerTrigger": str(config.get("eventhub.max_events_per_trigger")),
        "failOnDataLoss": "false",  # retention expiry is alerted on separately; don't crash-loop
    }


def start_bronze_stream(spark: SparkSession, config: Config):
    raw = spark.readStream.format("kafka").options(**eventhub_kafka_options(config)).load()
    bronze = raw.select(
        F.col("value").cast("string").alias("body"),
        F.col("key").cast("string").alias("partition_key"),
        F.col("partition").alias("eh_partition"),
        F.col("offset").alias("eh_offset"),
        F.col("timestamp").alias("eh_enqueued_ts"),
        F.current_timestamp().alias("_ingest_ts"),
        F.to_date(F.col("timestamp")).alias("_ingest_date"),
    )
    return (
        bronze.writeStream.format("delta")
        # HIVE-STYLE PARTITIONING by _ingest_date — the one place we use partitionBy, because it fits
        # the rules of thumb: (1) append-only, time-ordered data; (2) LOW cardinality (1 value/day);
        # (3) each partition is LARGE (~15-25 GB/day of raw JSON >> the 1 GB minimum per partition);
        # (4) every consumer filters by date (replays, retention deletes, VACUUM scope).
        # Never partition by machine_id/device_id: skewed sizes + thousands of tiny directories.
        .partitionBy("_ingest_date")
        .option("checkpointLocation", config.checkpoint("bronze", "iot_sensor_raw"))
        .trigger(processingTime=f"{config.get('streaming.bronze_trigger_seconds')} seconds")
        .queryName("bronze_iot_sensor_raw")
        .toTable(config.fq("bronze", "iot_sensor_raw"))
    )

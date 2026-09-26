"""
IoT silver: bronze.iot_sensor_raw -> silver.iot_sensor_reading + silver.iot_sensor_minute_agg

1. Parse the JSON body against an explicit schema (never infer in streaming).
   Unparseable / invalid readings go to quarantine.iot_sensor_reading.
2. Event-time watermark (10 min): readings up to 10 minutes late are still
   aggregated correctly; later ones are dropped from aggregates (kept in bronze).
3. dropDuplicatesWithinWatermark on (device_id, sensor_type, event_ts):
   gateways retransmit on network blips -> duplicates are removed with bounded state.
4. 1-minute tumbling-window aggregates per machine/sensor with anomaly flags
   (reading outside the sensor type's alarm limits).
"""

from __future__ import annotations

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType

from src.common.config import Config

SENSOR_SCHEMA = StructType(
    [
        StructField("device_id", StringType()),
        StructField("machine_id", StringType()),
        StructField("sensor_type", StringType()),
        StructField("reading_value", DoubleType()),
        StructField("unit", StringType()),
        StructField("event_ts", StringType()),
        StructField("firmware", StringType()),
    ]
)

# Alarm limits per sensor type (would come from the machine OEM spec / MES in production).
SENSOR_THRESHOLDS = [("temperature", 0.0, 95.0), ("vibration", 0.0, 9.0), ("pressure", 20.0, 160.0), ("spindle_speed", 0.0, 11000.0)]


def parsed_readings(spark: SparkSession, config: Config):
    src = spark.readStream.table(config.fq("bronze", "iot_sensor_raw"))
    parsed = src.withColumn("j", F.from_json("body", SENSOR_SCHEMA)).select(
        F.upper(F.trim("j.machine_id")).alias("machine_id"),
        F.upper(F.trim("j.device_id")).alias("device_id"),
        F.lower(F.trim("j.sensor_type")).alias("sensor_type"),
        F.col("j.reading_value").alias("reading_value"),
        F.col("j.unit").alias("unit"),
        F.to_timestamp("j.event_ts").alias("event_ts"),
        "eh_partition",
        "eh_offset",
        "eh_enqueued_ts",
        "_ingest_ts",
        "body",
    )
    is_valid = F.col("machine_id").isNotNull() & F.col("event_ts").isNotNull() & F.col("reading_value").isNotNull() & F.col("sensor_type").isNotNull()
    return parsed, is_valid


def start_silver_streams(spark: SparkSession, config: Config):
    parsed, is_valid = parsed_readings(spark, config)
    late = config.get("streaming.late_data_watermark")
    trigger = f"{config.get('streaming.silver_trigger_seconds')} seconds"

    invalid_q = (
        parsed.filter(~is_valid)
        .writeStream.format("delta")
        .option("checkpointLocation", config.checkpoint("quarantine", "iot_sensor_reading"))
        .trigger(processingTime=trigger)
        .queryName("quarantine_iot")
        .toTable(config.fq("quarantine", "iot_sensor_reading"))
    )

    clean = parsed.filter(is_valid).drop("body").withWatermark("event_ts", late).dropDuplicatesWithinWatermark(["device_id", "sensor_type", "event_ts"])

    readings_q = (
        clean.withColumn("event_date", F.to_date("event_ts"))
        .writeStream.format("delta")
        .option("checkpointLocation", config.checkpoint("silver", "iot_sensor_reading"))
        .trigger(processingTime=trigger)
        .queryName("silver_iot_sensor_reading")
        .toTable(config.fq("silver", "iot_sensor_reading"))
    )

    # thresholds are tiny + static -> an inline CASE expression (no join after a streaming aggregation)
    max_ok = F.lit(None).cast("double")
    min_ok = F.lit(None).cast("double")
    for sensor, lo, hi in SENSOR_THRESHOLDS:
        max_ok = F.when(F.col("sensor_type") == sensor, F.lit(hi)).otherwise(max_ok)
        min_ok = F.when(F.col("sensor_type") == sensor, F.lit(lo)).otherwise(min_ok)
    minute_agg = (
        clean.groupBy(F.window("event_ts", config.get("streaming.aggregation_window")), "machine_id", "sensor_type")
        .agg(
            F.count(F.lit(1)).alias("reading_count"),
            F.avg("reading_value").alias("avg_value"),
            F.min("reading_value").alias("min_value"),
            F.max("reading_value").alias("max_value"),
        )
        .select(
            F.col("window.start").alias("window_start"),
            F.col("window.end").alias("window_end"),
            F.to_date("window.start").alias("event_date"),
            "machine_id",
            "sensor_type",
            "reading_count",
            "avg_value",
            "min_value",
            "max_value",
        )
        .withColumn("is_anomaly", F.coalesce((F.col("max_value") > max_ok) | (F.col("min_value") < min_ok), F.lit(False)))
    )
    agg_q = (
        minute_agg.writeStream.format("delta")
        .outputMode("append")  # a window is emitted once, after the watermark passes it -> final values only
        .option("checkpointLocation", config.checkpoint("silver", "iot_sensor_minute_agg"))
        .trigger(processingTime=trigger)
        .queryName("silver_iot_sensor_minute_agg")
        .toTable(config.fq("silver", "iot_sensor_minute_agg"))
    )
    return [invalid_q, readings_q, agg_q]

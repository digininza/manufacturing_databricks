"""
Sends the synthetic IoT events to Event Hub so the streaming pipeline can be
demoed without real machines. Uses the Event Hub Kafka endpoint with the
SEND-only SAS policy (a different secret from the Listen policy Databricks uses).
"""

from __future__ import annotations

import json
from datetime import date

from pyspark.sql import SparkSession

from src.common.config import Config
from src.common.notebook_utils import get_secret
from src.datagen.generator import ManufacturingDataGenerator


def publish_synthetic_events(spark: SparkSession, config: Config, business_date: date, sensor_interval_minutes: int = 5) -> int:
    events = ManufacturingDataGenerator(day1=business_date, sensor_interval_minutes=sensor_interval_minutes).iot_events(business_date)
    df = spark.createDataFrame([(e["machine_id"], json.dumps(e)) for e in events], "key STRING, value STRING")
    conn = get_secret(config.get("eventhub.secret_scope"), "eventhub-machine-telemetry-send-conn")
    jaas = f'kafkashaded.org.apache.kafka.common.security.plain.PlainLoginModule required username="$ConnectionString" password="{conn}";'
    (
        df.write.format("kafka")
        .option("kafka.bootstrap.servers", f"{config.get('eventhub.namespace')}.servicebus.windows.net:9093")
        .option("topic", config.get("eventhub.hub_name"))
        .option("kafka.security.protocol", "SASL_SSL")
        .option("kafka.sasl.mechanism", "PLAIN")
        .option("kafka.sasl.jaas.config", jaas)
        .save()
    )
    return len(events)

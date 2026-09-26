# Databricks notebook source
# MAGIC %md
# MAGIC # IoT · send synthetic telemetry to Event Hub (demo only)
# MAGIC Includes an anomaly episode on `M-P02-01` (temperature/vibration above alarm limits
# MAGIC 13:00–15:00), a duplicate retransmission, and a late event.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from datetime import date

from src.streaming.eventhub_simulator import publish_synthetic_events

n = publish_synthetic_events(spark, config, date.fromisoformat(get_widget("business_date", "2026-09-24")), int(get_widget("interval_minutes", "5")))
print(f"sent {n} events to {config.get('eventhub.hub_name')}")

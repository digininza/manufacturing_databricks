# Databricks notebook source
# MAGIC %md
# MAGIC # IoT · Event Hub → bronze.iot_sensor_raw (continuous)
# MAGIC Runs as a **continuous job** (resources/jobs/mfg_iot_streaming.yml) with unlimited retries:
# MAGIC if the cluster dies, the job restarts and resumes from the checkpointed Event Hub offsets.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.streaming.eventhub_to_bronze import start_bronze_stream

query = start_bronze_stream(spark, config)
query.awaitTermination()

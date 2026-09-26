# Databricks notebook source
# MAGIC %md
# MAGIC # IoT · bronze → silver readings + 1-minute aggregates (continuous)
# MAGIC Three queries share one cluster: quarantine (unparseable), deduplicated readings, and
# MAGIC watermark-bounded 1-minute window aggregates with anomaly flags. `fact_machine_daily`
# MAGIC in gold reads `silver.iot_sensor_minute_agg`.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.streaming.sensor_silver import start_silver_streams

queries = start_silver_streams(spark, config)
spark.streams.awaitAnyTermination()  # any query failing fails the task -> job retry restarts all three

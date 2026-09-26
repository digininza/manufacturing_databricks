# Databricks notebook source
# MAGIC %md
# MAGIC # Demo 4 · Idempotency: run everything twice, nothing changes
# MAGIC Why it holds:
# MAGIC * **Auto Loader** remembers ingested files → no new bronze rows.
# MAGIC * **Silver** streaming checkpoint → no unprocessed bronze rows; and even a forced replay
# MAGIC   is a no-op because MERGE only applies rows with a NEWER `_sequence`.
# MAGIC * **Gold** CDF watermark → no changes → no affected dates; SCD2 hash unchanged → no new versions.
# MAGIC * **Snowflake** publish watermark → nothing to publish.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

from src.gold.dimensions import build_all_dimensions
from src.gold.facts import build_all_facts
from src.silver.processor import run_silver_entity

tables = [config.fq("silver", "mes_production_log"), config.fq("gold", "dim_machine"), config.fq("gold", "fact_production_daily")]
before = {t: spark.table(t).count() for t in tables}

ctx = RunContext(config, "demo_idempotency", new_run_id("demo4"), JOB_RUN_ID)
for name in registry:
    run_silver_entity(spark, ctx, registry[name])
build_all_dimensions(spark, ctx)
build_all_facts(spark, ctx)

after = {t: spark.table(t).count() for t in tables}
for t in tables:
    print(f"{t:<55} before={before[t]:>6} after={after[t]:>6} {'OK' if before[t] == after[t] else 'CHANGED!'}")
assert before == after, "re-run changed row counts: idempotency broken"

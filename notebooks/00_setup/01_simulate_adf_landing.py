# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Simulate ADF landing (demo without ADF / on-prem sources)
# MAGIC Writes the synthetic source extracts to ADLS **exactly as ADF's Copy activity does**:
# MAGIC parquet under `raw/<source>/<entity>/load_date=.../run_id=.../<run_id>.parquet` plus the
# MAGIC count manifest under `raw/_manifests/<entity>/<run_id>.json`.
# MAGIC
# MAGIC `day = 1` → initial load, `day = 2` → incremental (CDC updates/deletes, SCD2 changes, dirty rows).
# MAGIC Run day 1, run the pipeline, run day 2, run the pipeline again — then look at silver/gold.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

import json
from datetime import date

from src.datagen.generate_synthetic_data import RAW_PATHS
from src.datagen.generator import ManufacturingDataGenerator

DAY = int(get_widget("day", "1"))
DAY1 = date.fromisoformat(get_widget("day1_date", "2026-09-24"))
BREAK_MANIFEST = get_widget("break_manifest_for", "")  # demo: entity whose manifest over-states rows_copied

gen = ManufacturingDataGenerator(day1=DAY1)
batches = gen.generate_all()
raw_root = config.get("storage.raw_root")

for entity, entity_batches in batches.items():
    b = entity_batches[DAY - 1]
    folder = f"{raw_root}/{RAW_PATHS[entity]}/load_date={b.load_date}/run_id={b.run_id}"
    cols = list(dict.fromkeys(k for r in b.rows for k in r))  # ordered union of keys
    # land everything as STRING: bronze keeps source values verbatim, silver owns the casting
    df = spark.createDataFrame([tuple(None if r.get(c) is None else str(r[c]) for c in cols) for r in b.rows], ", ".join(f"`{c}` STRING" for c in cols))
    df.coalesce(1).write.mode("overwrite").parquet(f"{folder}/_tmp")
    part = [f.path for f in dbutils.fs.ls(f"{folder}/_tmp") if f.path.endswith(".parquet")][0]  # noqa: F821
    dbutils.fs.mv(part, f"{folder}/{b.run_id}.parquet")  # noqa: F821
    dbutils.fs.rm(f"{folder}/_tmp", recurse=True)  # noqa: F821
    manifest = b.manifest(rows_copied=len(b.rows) + 5 if entity == BREAK_MANIFEST else None)
    dbutils.fs.put(f"{raw_root}/_manifests/{entity}/{b.run_id}.json", json.dumps(manifest), overwrite=True)  # noqa: F821
    print(f"{entity:<24} day {DAY}: {len(b.rows):>4} rows -> {folder}")

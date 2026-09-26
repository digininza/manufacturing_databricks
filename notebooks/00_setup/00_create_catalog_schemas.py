# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Create catalog, schemas, control + gold tables
# MAGIC Replays `sql/databricks/*.sql` with `${catalog}` substituted. Idempotent: safe to run on every deploy.

# COMMAND ----------

# MAGIC %run ../_common/bootstrap

# COMMAND ----------

import re
from pathlib import Path

sql_dir = Path(REPO_ROOT) / "sql" / "databricks"
for sql_file in sorted(sql_dir.glob("*.sql")):
    text = re.sub(r"--[^\n]*", "", sql_file.read_text()).replace("${catalog}", config.catalog)
    for stmt in [s.strip() for s in text.split(";") if s.strip()]:
        print(f"[{sql_file.name}] {stmt.splitlines()[0][:100]}")
        try:
            spark.sql(stmt)
        except Exception as exc:
            if not stmt.upper().startswith("GRANT"):
                raise
            print(f"   WARNING grant skipped (create the Entra group / SP first): {str(exc)[:150]}")
print("catalog ready:", config.catalog)

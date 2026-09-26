# Databricks notebook source
# MAGIC %md
# MAGIC ### Shared bootstrap: `%run ../_common/bootstrap` at the top of every notebook
# MAGIC Puts the repo root on `sys.path` so `src.*` imports work, and reads the standard
# MAGIC job parameters that every task receives (environment, run_date, job_run_id).

# COMMAND ----------

import os
import sys
from pathlib import Path

REPO_ROOT = str(Path(os.getcwd()).resolve().parents[1])  # notebooks/<stage>/ -> repo root
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.common.config import load_config, load_registry  # noqa: E402
from src.common.notebook_utils import get_spark, get_widget, new_run_id  # noqa: E402
from src.framework.audit import AuditLogger  # noqa: E402
from src.framework.run_context import RunContext  # noqa: E402

ENV = get_widget("environment", "dev")
RUN_DATE = get_widget("run_date", "")
JOB_RUN_ID = get_widget("job_run_id", "interactive")
config = load_config(ENV)
registry = load_registry()
spark = get_spark()

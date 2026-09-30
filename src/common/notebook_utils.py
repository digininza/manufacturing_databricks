"""Helpers that make the same code run as a Databricks job task or locally."""

from __future__ import annotations

import uuid
from datetime import date


def get_spark():
    from pyspark.sql import SparkSession

    return SparkSession.builder.getOrCreate()


def _dbutils():
    try:
        from pyspark.dbutils import DBUtils  # available on Databricks only

        return DBUtils(get_spark())
    except Exception:
        return None


def get_widget(name: str, default: str = "") -> str:
    """Read a job parameter / notebook widget; default when running locally."""
    dbu = _dbutils()
    if dbu is None:
        return default
    try:
        dbu.widgets.text(name, default)
        value = dbu.widgets.get(name)
        return value if value != "" else default
    except Exception:
        return default


def get_secret(scope: str, key: str) -> str:
    """Resolve a secret from the Key Vault-backed secret scope. Never log the return value."""
    dbu = _dbutils()
    if dbu is None:
        raise RuntimeError(f"Secret {scope}/{key} requested outside Databricks")
    return dbu.secrets.get(scope=scope, key=key)


def require_https(url: str) -> str:
    """Only https:// may be opened (blocks file:// and custom schemes; bandit B310)."""
    if not url.lower().startswith("https://"):
        raise ValueError("refusing to call a non-https URL")
    return url


def new_run_id(prefix: str) -> str:
    return f"{prefix}_{date.today():%Y%m%d}_{uuid.uuid4().hex[:8]}"

"""
RunContext — one object passed to every step so run-level lineage is automatic.

Every row written by the platform carries `_pipeline_run_id`; every control/audit
row carries `run_id` + `job_run_id`. Given any gold row you can walk back:
gold._pipeline_run_id -> control.pipeline_run_log -> silver batch -> bronze
_ingestion_run_id -> ADF manifest -> ctl.ingestion_run_history -> ADF pipeline run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.common.config import Config


@dataclass
class RunContext:
    config: Config
    pipeline_name: str
    run_id: str
    job_run_id: str = "interactive"
    started_at: datetime = field(default_factory=datetime.utcnow)

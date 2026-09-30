"""
Alerting: posts a JSON payload to an Azure Logic App HTTP trigger, which fans out
to Email + a Microsoft Teams channel. The webhook URL (it contains a SAS token)
is a secret and is read from the Key Vault-backed secret scope — never hard-coded.

ADF uses the SAME Logic App (pipeline PL_90_Send_Alert), so the on-call engineer
gets one consistent alert format regardless of which platform failed.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from typing import Any, Dict

from src.common.logging_utils import get_logger
from src.framework.run_context import RunContext

log = get_logger(__name__)


def build_alert_payload(ctx: RunContext, severity: str, entity_name: str, message: str, extra: Dict[str, Any] = None) -> Dict[str, Any]:
    return {
        "platform": "DATABRICKS",
        "environment": ctx.config.environment,
        "severity": severity,  # CRITICAL | HIGH | WARNING
        "pipeline_name": ctx.pipeline_name,
        "entity_name": entity_name,
        "run_id": ctx.run_id,
        "job_run_id": ctx.job_run_id,
        "message": message[:2000],
        "details": extra or {},
        "raised_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
    }


def send_alert(ctx: RunContext, severity: str, entity_name: str, message: str, extra: Dict[str, Any] = None) -> None:
    payload = build_alert_payload(ctx, severity, entity_name, message, extra)
    log.error("ALERT %s", json.dumps(payload))
    try:
        from src.common.notebook_utils import get_secret, require_https

        url = get_secret(ctx.config.get("alerting.secret_scope"), ctx.config.get("alerting.webhook_secret_key"))
        req = urllib.request.Request(require_https(url), data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        # bandit B310 (urlopen scheme): scheme validated by require_https() above
        urllib.request.urlopen(req, timeout=15)  # nosec B310
    except Exception as exc:  # alerting must never mask the original failure
        log.warning("alert delivery failed (job-level email notification still fires): %s", exc)

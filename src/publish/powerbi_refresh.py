"""
Trigger the Power BI Import semantic-model refresh AFTER a reconciled Snowflake publish.

Why not a fixed schedule in the Power BI service? A 06:00 schedule refreshes even
when the pipeline is late or failed, so users see yesterday's data labelled as
today's. Event-driven refresh means Power BI only ever loads published, reconciled data.

Auth: Entra ID service principal (client-credentials). The SP is a Member of
the Power BI workspace; tenant setting "Service principals can use Fabric APIs" is on.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from src.common.notebook_utils import get_secret
from src.framework.run_context import RunContext


def _token(tenant_id: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://analysis.windows.net/powerbi/api/.default",
        }
    ).encode()
    req = urllib.request.Request(f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def trigger_refresh(ctx: RunContext, workspace_id: str, dataset_id: str) -> int:
    scope = ctx.config.get("alerting.secret_scope")
    token = _token(get_secret(scope, "powerbi-tenant-id"), get_secret(scope, "powerbi-sp-client-id"), get_secret(scope, "powerbi-sp-client-secret"))
    body = json.dumps({"notifyOption": "MailOnFailure", "type": "Full", "commitMode": "transactional", "applyRefreshPolicy": True}).encode()
    req = urllib.request.Request(
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}/refreshes",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status  # 202 Accepted

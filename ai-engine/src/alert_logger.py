import logging
from fastapi import APIRouter, Request
from datetime import datetime, timezone

logger = logging.getLogger("alert-logger")
router = APIRouter()


@router.post("/webhook/system-alerts")
async def log_system_alerts(request: Request):
    body = await request.json()
    alerts = body.get("alerts", [])

    for alert in alerts:
        name = alert.get("labels", {}).get("alertname", "Unknown")
        status = alert.get("status", "unknown")
        severity = alert.get("labels", {}).get("severity", "unknown")
        namespace = alert.get("labels", {}).get("namespace", "unknown")
        summary = alert.get("annotations", {}).get("summary", "")
        fired_at = alert.get("startsAt", "")

        logger.warning(
            f"SYSTEM_ALERT | "
            f"status={status} | "
            f"alert={name} | "
            f"severity={severity} | "
            f"namespace={namespace} | "
            f"summary={summary} | "
            f"firedAt={fired_at}"
        )

    return {"status": "ok", "logged": len(alerts)}

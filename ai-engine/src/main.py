import os
import asyncio
import logging
import httpx
import anthropic
import html
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from alert_logger import router as alert_logger_router
from pydantic import BaseModel, ValidationError
from datetime import datetime, timezone, timedelta
from collections import deque
from prometheus_client import Counter, Histogram, make_asgi_app
import time
import json
from remediation import should_remediate, remediate, ALERT_REMEDIATION_MAP

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        })

_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])

# Apply JSON formatter to uvicorn loggers
for _uvicorn_logger in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    _uv = logging.getLogger(_uvicorn_logger)
    _uv.handlers = [_handler]
    _uv.propagate = False
log = logging.getLogger("ai-engine")

app = FastAPI(title="AI Incident Engine")
app.include_router(alert_logger_router)

# --- Prometheus metrics ---

alerts_received_total = Counter(
    "ai_engine_alerts_received_total",
    "Total number of alert groups received via webhook",
)
alerts_analyzed_total = Counter(
    "ai_engine_alerts_analyzed_total",
    "Total number of alert groups successfully analyzed",
)
claude_errors_total = Counter(
    "ai_engine_claude_errors_total",
    "Total number of Claude API errors (all retries exhausted)",
)
telegram_errors_total = Counter(
    "ai_engine_telegram_errors_total",
    "Total number of Telegram delivery failures",
)
remediations_total = Counter(
    "ai_engine_remediations_total",
    "Total number of remediation actions taken",
    ["action", "status"],
)
claude_request_duration_seconds = Histogram(
    "ai_engine_claude_request_duration_seconds",
    "Duration of successful Claude API requests",
    buckets=[0.5, 1, 2, 5, 10, 30],
)

# Mount /metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# --- Config ---

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://loki-gateway.monitoring")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

_last_analyzed: dict[str, datetime] = {}
MAX_CLAUDE_CALLS_PER_HOUR = int(os.getenv("MAX_CLAUDE_CALLS_PER_HOUR", "10"))
_claude_call_times: deque = deque()
DEDUP_WINDOW_MINUTES = 30
MAX_LOG_LINES = 30
MAX_LOG_CHARS = 3000

CLAUDE_RETRY_DELAYS = [1, 2, 4]  # seconds between attempts

ALERT_PRIORITY = {
    "ChaosModeActive": 1,
    "CriticalErrorRate": 2,
    "HighErrorRate": 3,
    "SlowResponseTime": 4,
}


# --- Pydantic models ---

class AlertLabel(BaseModel):
    alertname: str = "Unknown"
    service: str = "unknown"
    severity: str = "unknown"


class AlertAnnotation(BaseModel):
    description: str = ""
    summary: str = ""


class Alert(BaseModel):
    status: str
    labels: AlertLabel
    annotations: AlertAnnotation = AlertAnnotation()


class WebhookPayload(BaseModel):
    alerts: list[Alert] = []


# --- Deduplication ---

def should_analyze(group_key: str) -> bool:
    if group_key not in _last_analyzed:
        return True
    elapsed = datetime.now(timezone.utc) - _last_analyzed[group_key]
    return elapsed > timedelta(minutes=DEDUP_WINDOW_MINUTES)


def pick_primary_alert(alerts: list[Alert]) -> Alert | None:
    """
    Select the highest‑priority *firing* alert.
    If no firing alerts exist, return None (ignore resolved-only groups).
    """
    if not alerts:
        return None

    firing = [a for a in alerts if a.status == "firing"]

    if not firing:
        return None

    firing.sort(key=lambda a: ALERT_PRIORITY.get(a.labels.alertname, 999))
    return firing[0]


# --- Message formatting ---

def format_message(
    alert_names: list[str],
    primary_name: str,
    metrics: dict,
    analysis: str,
    timestamp: str,
) -> str:
    alerts_header = primary_name
    if len(alert_names) > 1:
        others = [n for n in alert_names if n != primary_name]
        alerts_header += f" + {', '.join(others)}"

    error_rate = metrics.get("error_rate")
    request_rate = metrics.get("request_rate")
    p95 = metrics.get("p95_latency")
    chaos = metrics.get("chaos_mode")

    return (
        f"🚨 <b>AI Incident Analysis</b>\n"
        f"🔔 Alert: <code>{html.escape(alerts_header)}</code>\n"
        f"🕐 Time: {timestamp}\n\n"
        f"📊 <b>Metrics:</b>\n"
        f"- Error rate: {f'{error_rate:.1f}%' if error_rate is not None else 'N/A'}\n"
        f"- Request rate: {f'{request_rate:.3f} req/s' if request_rate is not None else 'N/A'}\n"
        f"- P95 latency: {f'{p95:.3f}s' if p95 is not None else 'N/A'}\n"
        f"- Chaos mode: {chaos}\n\n"
        f"🧠 <b>Analysis:</b>\n"
        f"{html.escape(analysis)}"
    )


def format_fallback_message(
    alert_names: list[str],
    primary_name: str,
    metrics: dict,
    timestamp: str,
) -> str:
    """Fallback message when Claude is unavailable — raw metrics only."""
    alerts_header = primary_name
    if len(alert_names) > 1:
        others = [n for n in alert_names if n != primary_name]
        alerts_header += f" + {', '.join(others)}"

    error_rate = metrics.get("error_rate")
    request_rate = metrics.get("request_rate")
    p95 = metrics.get("p95_latency")
    chaos = metrics.get("chaos_mode")

    return (
        f"⚠️ <b>Incident Alert</b>\n"
        f"🔔 Alert: <code>{html.escape(alerts_header)}</code>\n"
        f"🕐 Time: {timestamp}\n\n"
        f"📊 <b>Metrics:</b>\n"
        f"- Error rate: {f'{error_rate:.1f}%' if error_rate is not None else 'N/A'}\n"
        f"- Request rate: {f'{request_rate:.3f} req/s' if request_rate is not None else 'N/A'}\n"
        f"- P95 latency: {f'{p95:.3f}s' if p95 is not None else 'N/A'}\n"
        f"- Chaos mode: {chaos}\n\n"
        f"🧠 <b>Analysis:</b>\n"
        f"⚠️ AI analysis unavailable — showing raw metrics only"
    )


# --- Data collection with retry ---

async def fetch_with_retry(client: httpx.AsyncClient, url: str, params: dict, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = await client.get(url, params=params, timeout=5)
            return r.json()
        except Exception as e:
            log.warning(f"Attempt {attempt + 1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def get_metrics(job: str = "victim-service") -> dict:
    queries = {
        "error_rate": f'rate(http_requests_total{{job="{job}",status="500"}}[2m])/rate(http_requests_total{{job="{job}"}}[2m])*100',
        "request_rate": f'rate(http_requests_total{{job="{job}"}}[2m])',
        "p95_latency": f'histogram_quantile(0.95,rate(http_request_duration_seconds_bucket{{job="{job}"}}[2m]))',
        "chaos_mode": "chaos_mode_active",
    }
    results = {}
    async with httpx.AsyncClient() as client:
        for name, query in queries.items():
            data = await fetch_with_retry(client, f"{PROMETHEUS_URL}/api/v1/query", {"query": query})
            try:
                if data and data["data"]["result"]:
                    results[name] = float(data["data"]["result"][0]["value"][1])
                else:
                    results[name] = None
            except Exception:
                results[name] = None
    log.info(f"Metrics collected: {results}")
    return results


async def get_logs(namespace: str = "app") -> str:
    async with httpx.AsyncClient() as client:
        data = await fetch_with_retry(
            client,
            f"{LOKI_URL}/loki/api/v1/query_range",
            {"query": f'{{namespace="{namespace}"}}', "limit": MAX_LOG_LINES, "direction": "backward"},
        )
        if not data:
            return "Failed to fetch logs"
        try:
            logs = []
            for stream in data.get("data", {}).get("result", []):
                for _, line in stream.get("values", []):
                    logs.append(line)
            log.info(f"Fetched {len(logs)} log lines from Loki")
            result = "\n".join(logs[:MAX_LOG_LINES])
            return result[:MAX_LOG_CHARS]
        except Exception as e:
            log.error(f"Failed to parse logs: {e}")
            return "Failed to parse logs"


async def analyze_with_claude(alert_names: list[str], metrics: dict, logs: str) -> str | None:
    """
    Call Claude API with exponential backoff retry.
    Returns analysis text on success, None if all attempts fail.
    Delays: 1s, 2s, 4s between attempts (3 total).
    """
    now = datetime.now(timezone.utc)
    while _claude_call_times and now - _claude_call_times[0] > timedelta(hours=1):
        _claude_call_times.popleft()
    if len(_claude_call_times) >= MAX_CLAUDE_CALLS_PER_HOUR:
        log.warning("Claude rate limit reached (%d calls/hour), skipping", MAX_CLAUDE_CALLS_PER_HOUR)
        return None
    _claude_call_times.append(now)
    error_rate = metrics.get("error_rate")
    request_rate = metrics.get("request_rate")
    p95 = metrics.get("p95_latency")
    chaos = metrics.get("chaos_mode")

    prompt = f"""You are an expert SRE analyzing a Kubernetes incident.

Firing alerts: {", ".join(alert_names)}
Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}

Metrics:
- Error rate: {f"{error_rate:.1f}%" if error_rate is not None else "N/A"}
- Request rate: {f"{request_rate:.3f} req/s" if request_rate is not None else "N/A"}
- P95 latency: {f"{p95:.3f}s" if p95 is not None else "N/A"}
- Chaos mode: {chaos}

Recent logs:
{logs}

Provide a concise incident analysis:
1. Root cause (2-3 sentences)
2. Impact (1-2 sentences)
3. Actions (3 specific steps)

Be specific and actionable. Plain text only, no markdown formatting."""

    last_error = None
    for attempt, delay in enumerate(CLAUDE_RETRY_DELAYS, start=1):
        try:
            log.info(f"Claude API attempt {attempt}/{len(CLAUDE_RETRY_DELAYS)}")
            claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            start = time.monotonic()
            message = claude_client.messages.create(
                model="claude-opus-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            duration = time.monotonic() - start
            claude_request_duration_seconds.observe(duration)
            log.info(f"Claude responded in {duration:.2f}s")
            return message.content[0].text
        except Exception as e:
            last_error = e
            log.warning(f"Claude API attempt {attempt} failed: {e}")
            if attempt < len(CLAUDE_RETRY_DELAYS):
                await asyncio.sleep(delay)

    log.error(f"Claude API unavailable after {len(CLAUDE_RETRY_DELAYS)} attempts: {last_error}")
    claude_errors_total.inc()
    return None


async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not configured")
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code != 200:
                log.error(f"Telegram error {resp.status_code}: {resp.text}")
                telegram_errors_total.inc()
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        telegram_errors_total.inc()


@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        raw = await request.json()
        payload = WebhookPayload(**raw)
    except ValidationError as e:
        log.error(f"Invalid webhook payload: {e.errors()}")
        return {"status": "error", "detail": e.errors()}
    except Exception as e:
        log.error(f"Unexpected error parsing webhook: {e}")
        return {"status": "error", "detail": str(e)}

    firing = [a for a in payload.alerts if a.status == "firing"]
    if not firing:
        return {"status": "ok", "skipped": "no firing alerts"}

    alerts_received_total.inc()

    service = firing[0].labels.service
    alert_names = sorted({a.labels.alertname for a in firing})
    group_key = f"{service}:{':'.join(alert_names)}"

    log.info(f"Received alerts: {alert_names} for service: {service}")

    if not should_analyze(group_key):
        log.info(f"Skipping duplicate group: {group_key}")
        return {"status": "ok", "skipped": f"deduplicated < {DEDUP_WINDOW_MINUTES}min"}

    _last_analyzed[group_key] = now = datetime.now(timezone.utc)
    log.info(f"Dedup updated: {group_key} at {now.isoformat()}")

    primary = pick_primary_alert(firing)
    if not primary:
        return {"status": "ok", "skipped": "no alerts to process"}
    primary_name = primary.labels.alertname

    metrics = await get_metrics()
    logs = await get_logs()
    timestamp = datetime.now(timezone.utc).strftime('%H:%M UTC')

    analysis = await analyze_with_claude(alert_names, metrics, logs)

    if analysis is not None:
        alerts_analyzed_total.inc()
        message = format_message(alert_names, primary_name, metrics, analysis, timestamp)
    else:
        message = format_fallback_message(alert_names, primary_name, metrics, timestamp)

    await send_telegram(message)
    log.info(f"Message sent for group: {group_key}, ai_used={analysis is not None}")

    # --- Remediation ---
    if primary_name in ALERT_REMEDIATION_MAP and should_remediate(primary_name):
        result = remediate(primary_name)
        remediations_total.labels(action=result["action"], status=result["status"]).inc()
        log.info(f"Remediation result: {result}")
        if result["status"] == "success":
            await send_telegram(
                f"🔧 <b>Auto-remediation</b>\n"
                f"Action: rollout restart\n"
                f"Target: <code>{html.escape(result['detail'])}</code>\n"
                f"Triggered by: {html.escape(primary_name)}"
            )

    return {"status": "ok", "analyzed": group_key}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/live")
async def liveness():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    checks = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "anthropic_key": bool(ANTHROPIC_API_KEY),
    }
    ready = all(checks.values())
    status_code = 200 if ready else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if ready else "not ready", "checks": checks},
    )

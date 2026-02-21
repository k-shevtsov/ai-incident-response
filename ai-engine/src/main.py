import os
import httpx
import anthropic
from fastapi import FastAPI, Request
from datetime import datetime, timezone, timedelta

app = FastAPI(title="AI Incident Response Engine")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PROMETHEUS_URL = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
LOKI_URL = "http://loki-gateway.monitoring.svc.cluster.local"

# Дедупликация — храним время последнего анализа по алерту
last_analyzed: dict = {}
COOLDOWN_SECONDS = 300  # 5 минут между анализами одного алерта


async def get_prometheus_metrics(service: str) -> dict:
    queries = {
        "error_rate": f'rate(http_requests_total{{job="{service}",status="500"}}[5m]) / rate(http_requests_total{{job="{service}"}}[5m]) * 100',
        "request_rate": f'rate(http_requests_total{{job="{service}"}}[5m])',
        "p95_latency": f'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{job="{service}"}}[5m]))',
        "chaos_mode": "chaos_mode_active",
    }
    results = {}
    async with httpx.AsyncClient() as client:
        for name, query in queries.items():
            try:
                r = await client.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    params={"query": query},
                    timeout=5
                )
                data = r.json()
                if data["data"]["result"]:
                    results[name] = round(float(data["data"]["result"][0]["value"][1]), 3)
                else:
                    results[name] = "no data"
            except Exception as e:
                results[name] = f"error: {e}"
    return results


async def get_loki_logs(namespace: str, limit: int = 30) -> str:
    # Ищем только ошибки и предупреждения
    query = f'{{namespace="{namespace}"}} |~ "(?i)(error|warn|exception|500|failed)"'
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=5)

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "limit": limit,
                    "start": int(start.timestamp()),
                    "end": int(now.timestamp()),
                },
                timeout=5
            )
            data = r.json()
            logs = []
            for stream in data.get("data", {}).get("result", []):
                for _, line in stream.get("values", []):
                    logs.append(line)
            if logs:
                return "\n".join(logs[-20:])
            # Если ошибок нет — берём последние логи
            r2 = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": f'{{namespace="{namespace}"}}',
                    "limit": 10,
                    "start": int(start.timestamp()),
                    "end": int(now.timestamp()),
                },
                timeout=5
            )
            data2 = r2.json()
            logs2 = []
            for stream in data2.get("data", {}).get("result", []):
                for _, line in stream.get("values", []):
                    logs2.append(line)
            return "\n".join(logs2[-10:]) if logs2 else "No logs found"
        except Exception as e:
            return f"Error fetching logs: {e}"


async def analyze_with_claude(alert_name: str, metrics: dict, logs: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a DevOps engineer analyzing a Kubernetes incident. Be concise.

ALERT: {alert_name}

METRICS:
- Error rate: {metrics.get('error_rate')}%
- Request rate: {metrics.get('request_rate')} req/s
- P95 latency: {metrics.get('p95_latency')}s
- Chaos mode: {metrics.get('chaos_mode')}

RECENT ERROR LOGS:
{logs}

Provide analysis in exactly this format (keep it short):
**Root cause:** [1 sentence]
**Impact:** [1 sentence]  
**Actions:**
1. [action]
2. [action]
3. [action]"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


async def send_telegram(message: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        )


@app.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request):
    payload = await request.json()
    alerts = payload.get("alerts", [])
    processed = 0

    for alert in alerts:
        if alert.get("status") != "firing":
            continue

        alert_name = alert["labels"].get("alertname", "Unknown")
        service = alert["labels"].get("service", "victim-service")

        # Дедупликация
        now = datetime.now(timezone.utc)
        last = last_analyzed.get(alert_name)
        if last and (now - last).seconds < COOLDOWN_SECONDS:
            continue

        last_analyzed[alert_name] = now

        metrics = await get_prometheus_metrics(service)
        logs = await get_loki_logs("app")
        analysis = await analyze_with_claude(alert_name, metrics, logs)

        message = f"""🤖 *AI Incident Analysis*
🚨 *Alert:* `{alert_name}`
⏰ *Time:* {now.strftime('%H:%M UTC')}

📊 *Metrics:*
- Error rate: `{metrics.get('error_rate')}%`
- Request rate: `{metrics.get('request_rate')} req/s`
- P95 latency: `{metrics.get('p95_latency')}s`
- Chaos mode: `{metrics.get('chaos_mode')}`

🧠 *Analysis:*
{analysis}"""

        await send_telegram(message)
        processed += 1

    return {"status": "processed", "alerts": processed}


@app.get("/health")
async def health():
    return {"status": "ok"}

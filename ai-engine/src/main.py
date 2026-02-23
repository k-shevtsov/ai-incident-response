import os

import httpx
import anthropic
from fastapi import FastAPI, Request
from datetime import datetime, timezone

app = FastAPI(title="AI Incident Engine")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://loki-gateway.monitoring")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


async def get_metrics(job: str = "victim-service") -> dict:
    async with httpx.AsyncClient() as client:
        queries = {
            "error_rate": f'rate(http_requests_total{{job="{job}",status="500"}}[2m])/rate(http_requests_total{{job="{job}"}}[2m])*100',
            "request_rate": f'rate(http_requests_total{{job="{job}"}}[2m])',
            "p95_latency": f'histogram_quantile(0.95,rate(http_request_duration_seconds_bucket{{job="{job}"}}[2m]))',
            "chaos_mode": 'chaos_mode_active',
        }
        results = {}
        for name, query in queries.items():
            try:
                r = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
                data = r.json()
                if data["data"]["result"]:
                    results[name] = float(data["data"]["result"][0]["value"][1])
                else:
                    results[name] = None
            except Exception:
                results[name] = None
        return results


async def get_logs(namespace: str = "app", limit: int = 20) -> str:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={"query": f'{{namespace="{namespace}"}}', "limit": limit, "direction": "backward"},
                timeout=5
            )
            data = r.json()
            logs = []
            for stream in data.get("data", {}).get("result", []):
                for _, line in stream.get("values", []):
                    logs.append(line)
            return "\n".join(logs[:20]) if logs else "No logs available"
        except Exception as e:
            return f"Failed to fetch logs: {e}"


async def analyze_with_claude(alert_name: str, metrics: dict, logs: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are an expert SRE analyzing a Kubernetes incident.

Alert: {alert_name}
Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}

Metrics:
- Error rate: {metrics.get('error_rate', 'N/A')}%
- Request rate: {metrics.get('request_rate', 'N/A')} req/s
- P95 latency: {metrics.get('p95_latency', 'N/A')}s
- Chaos mode: {metrics.get('chaos_mode', 'N/A')}

Recent logs:
{logs}

Provide a concise incident analysis with:
1. Root cause (2-3 sentences)
2. Impact (1-2 sentences)
3. Actions (3 specific steps)

Be specific and actionable. Format with clear sections."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )


@app.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()
    alerts = body.get("alerts", [])

    for alert in alerts:
        if alert.get("status") != "firing":
            continue

        alert_name = alert["labels"].get("alertname", "Unknown")
        metrics = await get_metrics()
        logs = await get_logs()
        analysis = await analyze_with_claude(alert_name, metrics, logs)

        error_rate = metrics.get('error_rate')
        request_rate = metrics.get('request_rate')
        p95 = metrics.get('p95_latency')
        chaos = metrics.get('chaos_mode')

        message = f"""🚨 *AI Incident Analysis*
🔔 Alert: `{alert_name}`
🕐 Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}

📊 *Metrics:*
- Error rate: `{f"{error_rate:.1f}%" if error_rate is not None else "N/A"}`
- Request rate: `{f"{request_rate:.3f} req/s" if request_rate is not None else "N/A"}`
- P95 latency: `{f"{p95:.3f}s" if p95 is not None else "N/A"}`
- Chaos mode: `{chaos}`

🧠 *Analysis:*
{analysis}"""

        await send_telegram(message)

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}

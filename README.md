# AI Incident Response System

![CI](https://github.com/k-shevtsov/ai-incident-response/actions/workflows/ci.yaml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-34%20passed-brightgreen)

An autonomous incident response platform that detects anomalies in a Kubernetes service, analyzes them with Claude AI, and delivers actionable reports to Telegram — without human intervention.

> Built as a portfolio project to demonstrate production-grade DevOps and MLOps practices: observability, GitOps, CI/CD, and AI-augmented operations.

---

## Why This Project Matters

Modern systems generate too many alerts for on-call engineers to triage manually. This project explores **AI-Ops** — using AI to automate the first-response layer of incident management:

- **Reduces MTTR** — from alert to actionable analysis in ~30 seconds, no human triage needed
- **Cuts alert fatigue** — intelligent deduplication suppresses noise during prolonged incidents
- **Demonstrates full-stack DevOps** — observability pipeline, GitOps, CI/CD, and AI integration in one project
- **Production patterns** — retry logic, Pydantic validation, HTML-safe messaging, priority-based routing
- **Testable AI systems** — mocked external services, frozen time, endpoint integration tests

---

## Demo

**Chaos mode enabled → alerts fire → AI analysis delivered to Telegram in ~30 seconds:**

```
🚨 AI Incident Analysis
🔔 Alert: ChaosModeActive + CriticalErrorRate, HighErrorRate
🕐 Time: 17:21 UTC

📊 Metrics:
- Error rate: 100.0%
- Request rate: 0.122 req/s
- P95 latency: 1.938s
- Chaos mode: 1.0

🧠 Analysis:
Root Cause: Chaos mode is actively enabled, injecting failures into all
non-health business traffic. The underlying service is functional
(/health returns 200), but all business endpoints are failing.

Impact: Complete service outage for business traffic. Clients are
backing off — request rate dropped to 0.122 req/s. P95 latency of
1.938s confirms requests experience significant delays before failing.

Actions:
1. Disable chaos mode immediately via kubectl or API endpoint
2. Monitor error rate dropping back to baseline after disable
3. Add TTL/auto-expiration to chaos experiments to prevent recurrence
```

---

## How It Works

```
Victim Service → Prometheus → Alertmanager → AI Engine → Claude API → Telegram
      │               │                           │
      │         (metrics + logs)           Prometheus
      │                                       + Loki
      ▼
  /chaos/enable
  (simulate outage)
```

**Step by step:**
1. Victim Service exposes `/metrics` and `/chaos/enable` to simulate incidents
2. Prometheus scrapes metrics every 15s and evaluates alerting rules
3. Alertmanager routes `victim-service` alerts to AI Engine via webhook
4. AI Engine deduplicates, queries Prometheus + Loki for context
5. Enriched data is sent to Claude API for root cause analysis
6. Analysis is formatted and delivered to Telegram

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        k3d Cluster                          │
│                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │   Victim    │    │  Prometheus  │    │   AI Engine   │  │
│  │   Service   │───▶│  + Grafana   │───▶│  (FastAPI)    │  │
│  │  (FastAPI)  │    │  + Loki      │    │               │  │
│  │  /chaos/*   │    │  + Alertmgr  │    │  • dedup      │  │
│  └─────────────┘    └──────────────┘    │  • metrics    │  │
│         │                  │            │  • logs       │  │
│         │                  │            │  • Claude AI  │  │
│         ▼                  ▼            └───────┬───────┘  │
│  ┌─────────────┐    ┌──────────────┐            │          │
│  │   ingress   │    │    ArgoCD    │            │          │
│  │    nginx    │    │   (GitOps)   │            │          │
│  └─────────────┘    └──────────────┘            │          │
└─────────────────────────────────────────────────┼──────────┘
                                                  ▼
                                         ┌─────────────────┐
                                         │    Telegram     │
                                         │      Bot        │
                                         └─────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Container runtime | Docker + k3d (k3s in Docker) |
| Orchestration | Kubernetes 1.28 |
| Ingress | ingress-nginx |
| Metrics | Prometheus + kube-state-metrics + node-exporter |
| Visualization | Grafana |
| Logging | Loki + Promtail |
| Alerting | Alertmanager → webhook |
| AI analysis | Anthropic Claude API |
| Notification | Telegram Bot API |
| GitOps | ArgoCD |
| CI/CD | GitHub Actions + GHCR |
| IaC | Terraform (namespaces) |
| App framework | FastAPI + Pydantic |
| Testing | pytest + freezegun + FastAPI TestClient |

---

## Project Structure

```
ai-incident-response/
├── app/                        # Victim service (FastAPI)
│   └── main.py                 # /api/data, /chaos/*, /metrics
├── ai-engine/
│   └── src/
│       ├── main.py             # Webhook handler, dedup, Claude, Telegram
│       └── alert_logger.py     # System alerts logger
├── infra/
│   ├── k8s/
│   │   ├── base/               # Victim service manifests
│   │   ├── ai-engine/          # AI Engine manifests
│   │   ├── argocd-apps.yaml
│   │   └── argocd-ingress.yaml
│   ├── helm/
│   │   ├── prometheus-values.yaml
│   │   ├── alerting-rules.yaml
│   │   ├── loki-values.yaml
│   │   └── promtail-values.yaml
│   └── terraform/              # Namespace definitions
├── .github/workflows/
│   └── ci.yaml                 # Build → push to GHCR → ArgoCD sync
├── scripts/
│   ├── start-project.sh
│   ├── stop-project.sh
│   └── restart-project.sh
└── docs/adr/
    ├── 001-k3d-local-cluster.md
    └── 002-github-actions-ghcr.md
```

---

## Key Features

**Intelligent deduplication** — alerts with the same fingerprint are suppressed for 5 minutes to avoid Telegram spam during prolonged incidents.

**Priority-based alert routing** — when multiple alerts fire simultaneously, the AI Engine selects the most critical one as the primary (`ChaosModeActive > CriticalErrorRate > HighErrorRate > SlowResponseTime`).

**Full observability pipeline** — AI analysis is enriched with real-time Prometheus metrics (error rate, request rate, P95 latency) and recent Loki log lines before being sent to Claude.

**GitOps with ArgoCD** — every push to `main` triggers a GitHub Actions pipeline that builds a Docker image, pushes it to GHCR, and ArgoCD automatically syncs the cluster.

**HTML-safe Telegram messages** — Claude responses are escaped before injection into HTML-mode Telegram messages, preventing formatting errors from Markdown/HTML conflicts.

---

## Running Locally

### Prerequisites

- Docker
- k3d
- kubectl, helm
- Telegram Bot token + chat ID
- Anthropic API key

### Quick Start

```bash
git clone https://github.com/k-shevtsov/ai-incident-response
cd ai-incident-response

# Create cluster
k3d cluster create ai-incident \
  --servers 1 --agents 2 \
  --port "8080:80@loadbalancer" \
  --port "8443:443@loadbalancer" \
  --k3s-arg "--disable=traefik@server:0"

k3d kubeconfig merge ai-incident --kubeconfig-switch-context

# Add to /etc/hosts
echo "127.0.0.1 victim.local grafana.local argocd.local" | sudo tee -a /etc/hosts

# Install ingress-nginx
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.0/deploy/static/provider/cloud/deploy.yaml

# Install monitoring stack
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --values infra/helm/prometheus-values.yaml

helm install loki grafana/loki \
  --namespace monitoring --values infra/helm/loki-values.yaml

helm install promtail grafana/promtail \
  --namespace monitoring --values infra/helm/promtail-values.yaml

# Deploy apps
kubectl apply -f infra/k8s/base/
kubectl apply -f infra/helm/alerting-rules.yaml
kubectl apply -f infra/k8s/ai-engine/

# Create secrets
kubectl create secret generic ai-engine-secrets \
  --namespace ai-engine \
  --from-literal=telegram-token='YOUR_TOKEN' \
  --from-literal=telegram-chat-id='YOUR_CHAT_ID' \
  --from-literal=anthropic-api-key='YOUR_KEY'
```

### Testing the Pipeline

```bash
# Trigger chaos
curl -X POST http://victim.local:8080/chaos/enable
for i in {1..30}; do curl -s http://victim.local:8080/api/data > /dev/null; done

# Wait ~2 minutes for Telegram alert with AI analysis

# Restore
curl -X POST http://victim.local:8080/chaos/disable
```

---

## Services

| Service | URL |
|---------|-----|
| Victim Service | http://victim.local:8080 |
| Grafana | http://grafana.local:8080 (admin/admin123) |
| ArgoCD | https://argocd.local:8443 |

---

## Tests

```bash
cd ai-engine
pip3 install pytest freezegun httpx fastapi anthropic
python3 -m pytest tests/ -v
# 34 passed in 0.72s
```

Test coverage includes: deduplication logic with frozen time, alert priority selection, Pydantic payload validation, HTML escaping, and full endpoint integration tests with mocked external services.

---

## Future Improvements

- **Multi-service support** — route alerts from multiple services with per-service AI context
- **RAG from logs** — use vector search over historical logs to enrich Claude's context
- **Auto-remediation** — for known alert patterns, trigger `kubectl rollout restart` automatically
- **SLA-aware routing** — escalate to PagerDuty if incident persists beyond SLA threshold
- **Runbook integration** — attach relevant runbook links to each alert type in the analysis
- **Metrics dashboard** — track MTTR, alert volume, and AI analysis accuracy over time

---

## Architecture Decisions

- [ADR-001: k3d for local Kubernetes cluster](docs/adr/001-k3d-local-cluster.md)
- [ADR-002: GitHub Actions + GHCR for CI/CD](docs/adr/002-github-actions-ghcr.md)

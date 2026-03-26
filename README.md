# AI Incident Response System

![CI](https://github.com/k-shevtsov/ai-incident-response/actions/workflows/ci.yaml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-81%20passed-brightgreen)
![Helm](https://img.shields.io/badge/helm-chart-blue)

An autonomous incident response platform that detects anomalies in a Kubernetes service,
analyzes them with Claude AI, triggers automated remediation, creates a GitHub Issue audit
trail, and delivers actionable reports to Telegram — without human intervention.

> Built as a portfolio project to demonstrate production-grade SRE and DevOps practices:
> observability, GitOps, CI/CD, AI-augmented operations, and autonomous remediation.

---

## Why This Project Matters

Modern systems generate too many alerts for on-call engineers to triage manually.
This project implements a full **detect → analyze → act → audit** loop:

- **Reduces MTTR** — from alert to remediation in ~60 seconds, no human triage needed
- **Cuts alert fatigue** — intelligent deduplication suppresses noise during prolonged incidents
- **Autonomous remediation** — known failure modes trigger `kubectl rollout restart` automatically
- **Full audit trail** — every incident opens a GitHub Issue with AI analysis; closes on resolution
- **Production patterns** — retry logic, cooldown guards, dry-run mode, Pydantic validation, RBAC
- **Testable AI systems** — 81 unit tests with mocked external services and frozen time

---

## Demo

**Chaos mode enabled → alerts fire → AI analysis + auto-remediation in ~60 seconds:**

```
🚨 AI Incident Analysis
🔔 Alert: ChaosModeActive + CriticalErrorRate
🕐 Time: 17:21 UTC
📋 Issue: #6

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
backing off — request rate dropped to 0.122 req/s.

Actions:
1. Disable chaos mode immediately via kubectl or API endpoint
2. Monitor error rate dropping back to baseline after disable
3. Add TTL/auto-expiration to chaos experiments to prevent recurrence
```

```
🔧 Auto-remediation
Action: rollout restart
Target: app/victim-service
Triggered by: ChaosModeActive
```

---

## How It Works

```
Victim Service → Prometheus → Alertmanager → AI Engine ──→ Claude API → Telegram
      │               │                           │
      │         (metrics + logs)                  ├──→ Kubernetes SDK → rollout restart
      │                                           │
      ▼                                           └──→ GitHub Issues API → audit trail
  /chaos/enable
  (simulate outage)
```

**Step by step:**
1. Victim Service exposes `/metrics` and `/chaos/enable` to simulate incidents
2. Prometheus scrapes metrics every 15s and evaluates alerting rules
3. Alertmanager routes `victim-service` alerts to AI Engine via webhook
4. AI Engine deduplicates, queries Prometheus + Loki for context
5. Enriched data is sent to Claude API for root cause analysis
6. A GitHub Issue is opened with AI analysis, metrics table, and labels
7. If the alert is in `ALERT_REMEDIATION_MAP`, a rolling restart is triggered via Kubernetes SDK
8. Analysis + remediation result are delivered to Telegram
9. When the alert resolves, the GitHub Issue is closed with a resolution comment

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          k3d Cluster                             │
│                                                                  │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐  │
│  │   Victim    │    │  Prometheus  │    │     AI Engine      │  │
│  │   Service   │───▶│  + Grafana   │───▶│     (FastAPI)      │  │
│  │  (FastAPI)  │    │  + Loki      │    │                    │  │
│  │  /chaos/*   │    │  + Alertmgr  │    │  • dedup (30 min)  │  │
│  └─────────────┘    └──────────────┘    │  • Claude AI       │  │
│         │                  │            │  • remediation     │  │
│         │                  │            │  • GitHub Issues   │  │
│         ▼                  ▼            └────────┬───────────┘  │
│  ┌─────────────┐    ┌──────────────┐             │              │
│  │   ingress   │    │    ArgoCD    │             │              │
│  │    nginx    │    │   (GitOps)   │             │              │
│  └─────────────┘    └──────────────┘             │              │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │
                               ┌───────────────────┼───────────────┐
                               ▼                   ▼               ▼
                        ┌──────────┐       ┌──────────┐   ┌──────────────┐
                        │ Telegram │       │ GitHub   │   │ Kubernetes   │
                        │   Bot    │       │ Issues   │   │ rollout      │
                        └──────────┘       └──────────┘   │ restart      │
                                                           └──────────────┘
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
| AI analysis | Anthropic Claude API (claude-opus-4-6) |
| Notification | Telegram Bot API |
| Remediation | Kubernetes Python SDK (kubernetes==31.0.0) |
| Audit trail | GitHub Issues API |
| GitOps | ArgoCD |
| CI/CD | GitHub Actions |
| Packaging | Helm chart |
| IaC | Terraform (namespaces) |
| App framework | FastAPI + Pydantic |
| Testing | pytest + freezegun + FastAPI TestClient |

---

## Project Structure

```
ai-incident-response/
├── app/                              # Victim service (FastAPI)
│   └── main.py                       # /api/data, /chaos/*, /metrics
├── ai-engine/
│   ├── src/
│   │   ├── main.py                   # Webhook handler, dedup, Claude, Telegram
│   │   ├── remediation.py            # Autonomous remediation via Kubernetes SDK
│   │   ├── github_issues.py          # GitHub Issues create/close
│   │   └── alert_logger.py           # System alerts logger
│   └── tests/
│       ├── test_main.py              # 53 unit tests
│       ├── test_remediation.py       # 9 unit tests
│       └── test_github_issues.py     # 19 unit tests
├── infra/
│   ├── k8s/
│   │   ├── base/                     # Victim service manifests
│   │   ├── ai-engine/
│   │   │   ├── deployment.yaml
│   │   │   ├── rbac.yaml             # ServiceAccount + Role + RoleBinding
│   │   │   └── service-monitor.yaml
│   │   ├── network-policies/         # Default deny + allow rules
│   │   ├── argocd-apps.yaml
│   │   └── argocd-ingress.yaml
│   ├── helm/
│   │   ├── ai-engine/                # Helm chart (parametrized)
│   │   │   ├── Chart.yaml
│   │   │   ├── values.yaml
│   │   │   └── templates/
│   │   ├── prometheus-values.yaml
│   │   ├── alerting-rules.yaml
│   │   ├── loki-values.yaml
│   │   ├── promtail-values.yaml
│   │   └── ai-engine-dashboard.json  # Grafana dashboard
│   └── terraform/                    # Namespace definitions
├── .github/workflows/
│   └── ci.yaml                       # Lint → test → build → push
├── docs/
│   ├── adr/
│   │   ├── 001-k3d-local-cluster.md
│   │   ├── 002-github-actions-ghcr.md
│   │   └── 003-autonomous-remediation.md
│   └── runbooks/
│       └── chaos-mode.md
└── scripts/
```

---

## Key Features

**Intelligent deduplication** — alerts with the same fingerprint are suppressed for 30 minutes
to avoid Telegram spam during prolonged incidents.

**Priority-based alert routing** — when multiple alerts fire simultaneously, the AI Engine
selects the most critical one as the primary
(`ChaosModeActive > CriticalErrorRate > HighErrorRate > SlowResponseTime`).

**Autonomous remediation** — for known alert patterns, a rolling restart of the affected
deployment is triggered automatically via Kubernetes SDK. Protected by a 15-minute cooldown
and dry-run mode by default. See [ADR-003](docs/adr/003-autonomous-remediation.md).

**GitHub Issues audit trail** — every incident opens a GitHub Issue with AI analysis, metrics
table, and labels (`incident`, `auto-generated`, `chaos`, severity). Automatically closed with
a resolution comment when the alert resolves.

**Full observability pipeline** — AI analysis is enriched with real-time Prometheus metrics
(error rate, request rate, P95 latency) and recent Loki log lines before being sent to Claude.

**Helm chart** — parametrized chart with `values.yaml`, ConfigMap, HPA, namespace-aware RBAC.
Deploy to any environment without editing manifests.

**GitOps with ArgoCD** — every push to `main` triggers a GitHub Actions pipeline; ArgoCD
automatically syncs the cluster.

---

## Running Locally

### Prerequisites

- Docker, k3d, kubectl, helm
- Telegram Bot token + chat ID
- Anthropic API key
- GitHub Personal Access Token (scope: Issues read/write)

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

# /etc/hosts
echo "127.0.0.1 victim.local grafana.local argocd.local" | sudo tee -a /etc/hosts

# Install ingress-nginx and monitoring stack
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace

helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --values infra/helm/prometheus-values.yaml

helm install loki grafana/loki \
  --namespace monitoring --values infra/helm/loki-values.yaml

helm install promtail grafana/promtail \
  --namespace monitoring --values infra/helm/promtail-values.yaml

# Create secrets
kubectl create secret generic ai-engine-secrets \
  --namespace ai-engine \
  --from-literal=telegram-token='YOUR_TOKEN' \
  --from-literal=telegram-chat-id='YOUR_CHAT_ID' \
  --from-literal=anthropic-api-key='YOUR_ANTHROPIC_KEY' \
  --from-literal=github-token='YOUR_GITHUB_PAT'

# Deploy via Helm chart
helm install ai-engine ./infra/helm/ai-engine \
  --namespace ai-engine --create-namespace \
  --set config.githubRepo='YOUR_GITHUB_USER/ai-incident-response'

# Apply alerting rules and network policies
kubectl apply -f infra/helm/alerting-rules.yaml
kubectl apply -f infra/k8s/network-policies/
```

### Testing the Pipeline

```bash
# Trigger chaos
curl -X POST http://victim.local:8080/chaos/enable
for i in {1..100}; do curl -s http://victim.local:8080/api/data > /dev/null; done

# Wait ~2 minutes:
# 1. Telegram receives AI analysis with GitHub Issue link
# 2. GitHub Issue opens automatically with labels
# 3. If REMEDIATION_ENABLED=true — rollout restart triggers

# Restore
curl -X POST http://victim.local:8080/chaos/disable
# GitHub Issue closes automatically with resolution comment
```

### Enable Live Remediation

```bash
# Verify RBAC first
kubectl auth can-i patch deployments \
  --namespace=app \
  --as=system:serviceaccount:ai-engine:ai-engine
# Expected: yes

# Enable
helm upgrade ai-engine ./infra/helm/ai-engine \
  --reuse-values \
  --set config.remediationEnabled=true \
  --namespace ai-engine
```

---

## Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Victim Service | http://victim.local:8080 | — |
| Grafana | http://grafana.local:8080 | admin / admin123 |
| ArgoCD | https://argocd.local:8443 | admin / see secret |

---

## Tests

```bash
cd ai-engine
pip install pytest freezegun httpx fastapi anthropic kubernetes pytest-asyncio
python3 -m pytest tests/ -v
# 81 passed in ~1.4s
```

| Test file | Coverage |
|-----------|----------|
| `test_main.py` | Dedup, priority routing, payload validation, Claude retry, webhook endpoint |
| `test_remediation.py` | Cooldown logic, dry-run, K8s patch, 403 error handling |
| `test_github_issues.py` | Create/close Issues, dedup by issue_key, API errors, label building |

---

## Architecture Decisions

| ADR | Decision |
|-----|----------|
| [ADR-001](docs/adr/001-k3d-local-cluster.md) | k3d for local Kubernetes cluster |
| [ADR-002](docs/adr/002-github-actions-ghcr.md) | GitHub Actions + GHCR for CI/CD |
| [ADR-003](docs/adr/003-autonomous-remediation.md) | Autonomous remediation via rollout restart |

---

## Runbooks

- [Chaos Mode Incident](docs/runbooks/chaos-mode.md) — what the system does automatically and when manual intervention is needed

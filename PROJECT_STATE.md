# Project State & Recovery Guide

## Quick Start After Reboot
```bash
# 1. Start Docker (if not running)
sudo systemctl start docker

# 2. Start cluster
k3d cluster start ai-incident
k3d kubeconfig merge ai-incident --kubeconfig-switch-context

# 3. Verify everything is up
kubectl get pods -A
curl http://victim.local:8080/health
```

## Cluster Info

- **k3d cluster**: ai-incident (1 server + 2 agents + loadbalancer)
- **Traefik**: disabled (using ingress-nginx instead)
- **Ports**: 8080→80, 8443→443

## /etc/hosts
```
127.0.0.1 victim.local grafana.local argocd.local
```

## Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Victim Service | http://victim.local:8080 | - |
| Grafana | http://grafana.local:8080 | admin/admin123 |
| ArgoCD | https://argocd.local:8443 | admin/see secret below |
| Prometheus | port-forward 9090 | - |
| Alertmanager | port-forward 9093 | - |
```bash
# ArgoCD password
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath='{.data.password}' | base64 -d && echo
```

## Namespaces

- **app** — victim-service
- **monitoring** — Prometheus, Grafana, Loki, Promtail, Alertmanager
- **ai-engine** — AI Engine
- **argocd** — ArgoCD
- **ingress-nginx** — ingress controller

## Secrets (stored in cluster, NOT in git)
```bash
# Alertmanager telegram token
kubectl get secret alertmanager-kube-prometheus-stack-alertmanager \
  -n monitoring -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d

# AI Engine secrets
kubectl get secret ai-engine-secrets -n ai-engine -o yaml
```

## If Cluster Needs Full Rebuild
```bash
k3d cluster delete ai-incident

k3d cluster create ai-incident \
  --servers 1 \
  --agents 2 \
  --port "8080:80@loadbalancer" \
  --port "8443:443@loadbalancer" \
  --k3s-arg "--disable=traefik@server:0"

k3d kubeconfig merge ai-incident --kubeconfig-switch-context

# Import all images
k3d image import \
  grafana/grafana:11.6.1 \
  quay.io/prometheus/prometheus:v3.4.0 \
  quay.io/prometheus/alertmanager:v0.28.1 \
  quay.io/prometheus/node-exporter:v1.9.1 \
  registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.12.0 \
  grafana/loki:3.4.2 \
  grafana/promtail:3.4.2 \
  victim-service:local \
  ai-engine:local \
  quay.io/argoproj/argocd:v2.13.0 \
  registry.k8s.io/ingress-nginx/controller:v1.12.0 \
  -c ai-incident

# Namespaces
kubectl create namespace app
kubectl create namespace monitoring
kubectl create namespace ai-engine
kubectl create namespace argocd

# ingress-nginx
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.0/deploy/static/provider/cloud/deploy.yaml

# Helm stacks
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --values infra/helm/prometheus-values.yaml \
  --values infra/helm/alertmanager-values.yaml \
  --timeout 10m

helm install loki grafana/loki \
  --namespace monitoring \
  --values infra/helm/loki-values.yaml

helm install promtail grafana/promtail \
  --namespace monitoring \
  --values infra/helm/promtail-values.yaml

# Apps
kubectl apply -f infra/k8s/base/
kubectl apply -f infra/helm/alerting-rules.yaml
kubectl apply -f infra/k8s/ai-engine/

# Secrets (replace with real values)
kubectl create secret generic ai-engine-secrets \
  --namespace ai-engine \
  --from-literal=telegram-token='YOUR_TOKEN' \
  --from-literal=telegram-chat-id='84982959' \
  --from-literal=anthropic-api-key='YOUR_KEY'

# Alertmanager config
# See infra/helm/alertmanager-values.yaml - add bot_token manually

# ArgoCD
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.13.0/manifests/install.yaml
kubectl apply -f infra/k8s/argocd-ingress.yaml
kubectl apply -f infra/k8s/argocd-apps.yaml
```

## Chaos Mode Testing
```bash
# Enable chaos
curl -X POST http://victim.local:8080/chaos/enable
for i in {1..30}; do curl -s http://victim.local:8080/api/data > /dev/null; done
# Wait 1-2 min for Telegram alerts with AI analysis

# Disable chaos
curl -X POST http://victim.local:8080/chaos/disable
```

## Week Status

- Week 1: k3d cluster, victim-service, Terraform ✅
- Week 2: Prometheus, Grafana, Loki, Alertmanager, Telegram ✅
- Week 3: AI Engine with Claude analysis ✅
- Week 4: GitHub Actions CI/CD, ArgoCD GitOps, AI Engine v2 ✅

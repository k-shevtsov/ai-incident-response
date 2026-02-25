#!/bin/bash

set -euo pipefail

CLUSTER_NAME="ai-incident"
LB_NAME="k3d-${CLUSTER_NAME}-serverlb"
WAIT_TIMEOUT=120  # seconds

ok()   { echo "✔ $*"; }
warn() { echo "⚠ $*"; }
fail() { echo "❌ $*"; exit 1; }
step() { echo; echo "========================================"; echo "▶ $*"; echo "========================================"; }

# ------------------------------------------------
step "Checking port 80 availability"
# ------------------------------------------------
if sudo lsof -i :80 2>/dev/null | grep -q "apache2"; then
    fail "Apache is running and blocking port 80. Run: sudo systemctl disable --now apache2"
fi
ok "Port 80 is free"

# ------------------------------------------------
step "Starting Docker"
# ------------------------------------------------
if ! docker info &>/dev/null; then
    sudo systemctl start docker
    sleep 3
fi
ok "Docker is running"

# ------------------------------------------------
step "Starting k3d cluster: ${CLUSTER_NAME}"
# ------------------------------------------------
if ! k3d cluster list | grep -q "${CLUSTER_NAME}"; then
    fail "Cluster '${CLUSTER_NAME}' not found. Create it first (see README)."
fi

k3d cluster start "${CLUSTER_NAME}"
sleep 3
ok "Cluster started"

# ------------------------------------------------
step "Checking loadbalancer"
# ------------------------------------------------
if ! docker ps --format '{{.Names}}' | grep -q "${LB_NAME}"; then
    warn "Loadbalancer not running — attempting restart..."
    docker start "${LB_NAME}" || fail "Failed to start loadbalancer. Run: docker logs ${LB_NAME}"
    sleep 3
fi
ok "Loadbalancer is running"

# ------------------------------------------------
step "Switching kubeconfig context"
# ------------------------------------------------
k3d kubeconfig merge "${CLUSTER_NAME}" --kubeconfig-switch-context
sleep 1
ok "Context switched"

# ------------------------------------------------
step "Checking nodes"
# ------------------------------------------------
kubectl get nodes

# ------------------------------------------------
step "Waiting for all pods to become Ready (timeout: ${WAIT_TIMEOUT}s)"
# ------------------------------------------------
SECONDS=0
while true; do
    NOT_READY=$(kubectl get pods -A --no-headers 2>/dev/null \
        | grep -v -E "Running|Completed" || true)

    if [ -z "$NOT_READY" ]; then
        ok "All pods are Ready"
        break
    fi

    if [ "$SECONDS" -ge "$WAIT_TIMEOUT" ]; then
        warn "Timeout reached. Some pods are still not ready:"
        echo "$NOT_READY"
        warn "Continue anyway — check manually with: kubectl get pods -A"
        break
    fi

    echo "⏳ Waiting... (${SECONDS}s / ${WAIT_TIMEOUT}s)"
    echo "$NOT_READY"
    sleep 5
done

# ------------------------------------------------
step "Pod status summary"
# ------------------------------------------------
for ns in ingress-nginx monitoring app ai-engine argocd; do
    echo "--- $ns ---"
    kubectl get pods -n "$ns" --no-headers 2>/dev/null || warn "Namespace $ns not found"
done

# ------------------------------------------------
step "Checking secrets"
# ------------------------------------------------
if ! kubectl get secret ai-engine-secrets -n ai-engine &>/dev/null; then
    warn "Secret 'ai-engine-secrets' not found in ai-engine namespace!"
    warn "Alerts will NOT be sent to Telegram. Create the secret:"
    echo
    echo "  kubectl create secret generic ai-engine-secrets \\"
    echo "    --namespace ai-engine \\"
    echo "    --from-literal=telegram-token='YOUR_TOKEN' \\"
    echo "    --from-literal=telegram-chat-id='YOUR_CHAT_ID' \\"
    echo "    --from-literal=anthropic-api-key='YOUR_KEY'"
    echo
else
    ok "Secret ai-engine-secrets exists"
fi

# ------------------------------------------------
step "Health check: victim-service"
# ------------------------------------------------
if curl -sf http://victim.local:8080/health &>/dev/null; then
    ok "victim-service is healthy"
else
    warn "victim-service health check failed — ingress may still be starting"
fi

# ------------------------------------------------
step "Startup complete"
# ------------------------------------------------
echo
echo "  Victim Service : http://victim.local:8080"
echo "  Grafana        : http://grafana.local:8080  (admin/admin123)"
echo "  ArgoCD         : https://argocd.local:8443"
echo

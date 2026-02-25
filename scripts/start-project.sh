#!/bin/bash

set -e

echo "========================================"
echo "▶ Starting Docker"
echo "========================================"
sudo systemctl start docker || sudo systemctl restart docker
sleep 2

echo
echo "========================================"
echo "▶ Starting k3d cluster: ai-incident"
echo "========================================"
k3d cluster start ai-incident
sleep 3

echo
echo "========================================"
echo "▶ Switching kubeconfig context"
echo "========================================"
k3d kubeconfig merge ai-incident --kubeconfig-switch-context
sleep 1

echo
echo "========================================"
echo "▶ Checking nodes"
echo "========================================"
kubectl get nodes
echo

echo "========================================"
echo "▶ Waiting for all pods to become Ready"
echo "========================================"

while true; do
    NOT_READY=$(kubectl get pods -A --no-headers | grep -v "Running" | grep -v "Completed" || true)
    if [ -z "$NOT_READY" ]; then
        echo "✔ All pods are Ready"
        break
    else
        echo "⏳ Still initializing..."
        echo "$NOT_READY"
        sleep 5
    fi
done

echo
echo "========================================"
echo "▶ Checking ingress-nginx"
echo "========================================"
kubectl get pods -n ingress-nginx

echo
echo "========================================"
echo "▶ Checking ArgoCD"
echo "========================================"
kubectl get pods -n argocd

echo
echo "========================================"
echo "▶ Checking Monitoring Stack (Prometheus, Grafana, Loki, Promtail)"
echo "========================================"
kubectl get pods -n monitoring

echo
echo "========================================"
echo "▶ Checking victim-service"
echo "========================================"
kubectl get pods -n app

echo
echo "========================================"
echo "▶ Health check: victim-service"
echo "========================================"
curl -v http://victim.local:8080/health || echo "Health check failed"

echo
echo "========================================"
echo "▶ Startup complete"
echo "========================================"

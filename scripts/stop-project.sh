#!/bin/bash

set -euo pipefail

CLUSTER_NAME="ai-incident"

ok()   { echo "✔ $*"; }
warn() { echo "⚠ $*"; }
step() { echo; echo "========================================"; echo "▶ $*"; echo "========================================"; }

# ------------------------------------------------
step "Stopping k3d cluster: ${CLUSTER_NAME}"
# ------------------------------------------------
if k3d cluster list | grep -q "${CLUSTER_NAME}"; then
    k3d cluster stop "${CLUSTER_NAME}"
    ok "Cluster stopped"
else
    warn "Cluster '${CLUSTER_NAME}' not found — already stopped or deleted"
fi

# ------------------------------------------------
step "Cluster status"
# ------------------------------------------------
k3d cluster list

# ------------------------------------------------
step "Reminder: secrets are lost on cluster recreate"
# ------------------------------------------------
warn "If you recreate the cluster, secrets must be re-created manually:"
echo "  - ai-engine-secrets (telegram-token, telegram-chat-id, anthropic-api-key)"
echo "  - alertmanager config secret"
echo "  See README or PROJECT_STATE.md for commands."

# ------------------------------------------------
step "Docker"
# ------------------------------------------------
echo "Stop Docker too? (y/N)"
read -r -t 10 ANSWER || ANSWER="n"
if [[ "${ANSWER,,}" == "y" ]]; then
    sudo systemctl stop docker
    ok "Docker stopped"
else
    ok "Docker left running"
fi

# ------------------------------------------------
step "Project stopped"
# ------------------------------------------------

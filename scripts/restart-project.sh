#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ok()   { echo "✔ $*"; }
step() { echo; echo "========================================"; echo "▶ $*"; echo "========================================"; }

step "Restarting project"

step "Stopping cluster"
bash "${SCRIPT_DIR}/stop-project.sh"

step "Starting cluster"
bash "${SCRIPT_DIR}/start-project.sh"

step "Restart complete"

#!/bin/bash

set -e

echo "========================================"
echo "▶ Restarting project"
echo "========================================"

# Stop
echo
echo "▶ Stopping cluster..."
~/ai-incident-response/scripts/stop-project.sh

# Start
echo
echo "▶ Starting cluster..."
~/ai-incident-response/scripts/start-project.sh

echo
echo "========================================"
echo "▶ Final health check"
echo "========================================"

curl -v http://victim.local:8080/health || echo "Health check failed"

echo
echo "========================================"
echo "▶ Restart complete"
echo "========================================"

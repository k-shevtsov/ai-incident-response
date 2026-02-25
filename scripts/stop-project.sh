#!/bin/bash

set -e

echo "========================================"
echo "▶ Stopping k3d cluster: ai-incident"
echo "========================================"
k3d cluster stop ai-incident || echo "Cluster already stopped"
sleep 2

echo
echo "========================================"
echo "▶ Checking cluster status"
echo "========================================"
k3d cluster list

echo
echo "========================================"
echo "▶ Stopping Docker (optional)"
echo "========================================"
# Uncomment if you want Docker to stop too:
# sudo systemctl stop docker

echo
echo "========================================"
echo "▶ Checking Docker status"
echo "========================================"
sudo systemctl status docker --no-pager || true

echo
echo "========================================"
echo "▶ Project stopped"
echo "========================================"

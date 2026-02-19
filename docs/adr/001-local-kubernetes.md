# ADR 001: Choosing k3d for Local Kubernetes

## Status
Accepted

## Context
A local Kubernetes environment is required for development and testing.
Considered options: minikube, kind, k3d, Docker Desktop.

## Decision
k3d (k3s running in Docker) has been selected.

## Rationale
- Runs on top of an existing Docker installation
- Very fast startup (< 30 seconds)
- Supports multi‑node clusters with a single command
- Consumes fewer resources compared to minikube

## Consequences
- Introduces a dependency on the Docker daemon
- Not fully identical to production EKS/GKE environments, but sufficiently close for development needs.

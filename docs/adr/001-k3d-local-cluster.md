# ADR-001: k3d for Local Kubernetes Development

## Status
Accepted

## Date
2026-02

## Context

The project requires a local Kubernetes environment for developing and testing
an observability stack (Prometheus, Grafana, Loki, Alertmanager) alongside
application workloads and an AI engine. The environment must support:

- Multiple nodes to simulate realistic scheduling
- LoadBalancer services with port mapping to localhost
- Fast iteration — frequent cluster recreates during development
- Low resource consumption on a developer laptop

## Considered Options

| Option | Pros | Cons |
|--------|------|------|
| **k3d** | Fast startup (~15s), multi-node, runs in Docker, lightweight | Not identical to EKS/GKE, k3s-specific quirks |
| **minikube** | Mature, well-documented, close to upstream k8s | Slow startup, heavy resource usage, separate VM |
| **kind** | Fast, upstream k8s, good for CI | No built-in LoadBalancer, harder ingress setup |
| **Docker Desktop** | Zero setup on Mac/Windows | Proprietary, single-node, licensing restrictions |

## Decision

**k3d** was selected as the local Kubernetes runtime.

## Rationale

k3d runs k3s inside Docker containers, which means it reuses the existing
Docker daemon with no additional VM overhead. Key factors:

- Cluster creation with port mappings takes ~15 seconds
- Multi-node clusters (1 server + 2 agents) are created with a single command
- LoadBalancer port mapping (`--port "8080:80@loadbalancer"`) works out of the box
- Traefik can be disabled in favour of ingress-nginx with a single flag
- Images can be imported directly into the cluster with `k3d image import`
- Clusters can be fully recreated in CI-like fashion during development

## Consequences

**Positive:**
- Fast feedback loop — full stack up in under 2 minutes after images are imported
- Multi-node scheduling mirrors production behaviour for pod distribution
- Identical workflow on any machine with Docker installed

**Negative:**
- k3s differs from upstream Kubernetes in minor ways (e.g. bundled components)
- Port mappings are lost if Docker restarts — requires cluster recreation (handled in `start-project.sh`)
- Not suitable for production or performance testing

## Notes

Traefik (bundled with k3s) was explicitly disabled in favour of ingress-nginx
for closer parity with production environments and better Ingress compatibility:
```bash
k3d cluster create ai-incident \
  --k3s-arg "--disable=traefik@server:0"
```

# ADR-002: GitHub Actions + GHCR for CI/CD

## Status
Accepted

## Date
2026-02

## Context

The project requires a CI/CD pipeline that automatically builds Docker images,
pushes them to a registry, and triggers deployment to the local k3d cluster
via ArgoCD GitOps. The pipeline must be:

- Free for a personal/portfolio project
- Tightly integrated with the GitHub repository
- Compatible with ArgoCD image tracking

## Considered Options

| Option | Pros | Cons |
|--------|------|------|
| **GitHub Actions + GHCR** | Native GitHub integration, free for public repos, no external accounts | GHCR namespace limitations (see below) |
| **GitHub Actions + Docker Hub** | Well-known, widely supported | Rate limits on free tier, separate credentials |
| **GitLab CI + GitLab Registry** | Fully integrated, no rate limits | Requires migrating repo to GitLab |
| **CircleCI + Docker Hub** | Powerful, flexible | Additional account, free tier limitations |

## Decision

**GitHub Actions** for CI with **GitHub Container Registry (GHCR)** as the
image registry.

## Rationale

Since the source code is already hosted on GitHub, using GitHub Actions and
GHCR eliminates the need for external accounts or credentials. GHCR images
are private by default and access is controlled via GitHub tokens, which
GitHub Actions provides automatically via `GITHUB_TOKEN`.

## Implementation Details

### 1. Required permissions

GitHub Actions requires explicit permissions to push images to GHCR:
```yaml
permissions:
  contents: read
  packages: write
```

Without `packages: write` the push step fails with a 403 error even though
`GITHUB_TOKEN` is automatically available.

### 2. GHCR namespace constraint

GHCR only supports one level of nesting after the username:
```
❌  ghcr.io/username/repo/image-name
✅  ghcr.io/username/image-name
```

This means all images must use a flat naming scheme regardless of the
repository structure. In this project:
```
ghcr.io/k-shevtsov/victim-service:latest
ghcr.io/k-shevtsov/ai-engine:latest
```

### 3. Pipeline flow
```
Push to main
    │
    ▼
GitHub Actions
    ├── Build victim-service image
    ├── Push → ghcr.io/k-shevtsov/victim-service:latest
    ├── Build ai-engine image
    └── Push → ghcr.io/k-shevtsov/ai-engine:latest
                        │
                        ▼
                    ArgoCD
              (detects new image tag)
                        │
                        ▼
              kubectl apply (k3d cluster)
```

## Consequences

**Positive:**
- Zero additional accounts or secrets — `GITHUB_TOKEN` is sufficient
- Images are versioned by Git SHA and `latest` tag simultaneously
- ArgoCD can track `latest` tag and auto-sync on every push

**Negative:**
- GHCR flat namespace requires unique image names across all repositories
- Images are tied to the GitHub account — migration requires re-tagging

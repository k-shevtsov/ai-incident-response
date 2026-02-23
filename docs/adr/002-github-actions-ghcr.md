# ADR-002: GitHub Actions and GHCR Configuration

## Context
When setting up CI/CD pipeline we encountered two issues with GitHub Container Registry.

## Decisions

### 1. Permissions for GHCR
GitHub Actions requires explicit permissions to publish images:
```yaml
permissions:
  contents: read
  packages: write
```

### 2. GHCR namespace limitation
GHCR only supports one level of nesting after username:
- ❌ `ghcr.io/username/repo/image`
- ✅ `ghcr.io/username/image`

## Consequences
All Docker image tags in CI/CD must use flat namespace format.

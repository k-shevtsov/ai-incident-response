# ADR-003: Autonomous Remediation via Kubernetes Rollout Restart

**Status:** Accepted  
**Date:** 2026-03-26  
**Author:** Kostiantyn Shevtsov  
**Deciders:** Solo project — decision made after reviewing SRE incident patterns  
**Related:** ADR-001 (AI Engine Architecture), ADR-002 (Alert Routing Strategy)

---

## Context

The ai-engine detects incidents via Alertmanager webhooks and analyzes them using the
Claude API. After implementing alert detection (Week 3) and AI analysis (Week 5), the
system could identify incidents but required manual intervention to resolve them.

For `ChaosModeActive` and `CriticalErrorRate` alerts, the root cause is consistently
a degraded application state in the `victim-service` deployment — either due to injected
chaos faults or accumulated error state. Observed incident data shows that a pod restart
resolves these conditions reliably within the lab environment.

The goal was to close the loop: **detect → analyze → act**, without requiring human
intervention for known, recoverable failure modes. This is consistent with how mature
SRE teams operate: toil reduction through automation of well-understood remediation
procedures.

Key constraints that shaped the decision:

- The ai-engine runs inside the cluster with a dedicated `ServiceAccount`
- Remediation must be safe to enable/disable at runtime without redeployment
- A single misconfigured action must not cascade into a remediation loop
- The solution must be auditable — every action logged and traceable

---

## Considered Alternatives

### Option A: Scale deployment to zero, then back to desired replicas

Patch `spec.replicas` to `0`, wait, then restore to the original count.

**Pros:**
- Forces complete pod termination — no lingering state

**Cons:**
- Causes guaranteed downtime between scale-down and scale-up
- Requires storing the original replica count and a two-step patch
- More complex rollback if the restore step fails
- Not idiomatic Kubernetes — operators and HPA may interfere

**Verdict:** Rejected. Unnecessary downtime for a stateless service.

---

### Option B: Delete all pods in the namespace directly

Use `CoreV1Api.delete_namespaced_pod` for each pod matching the deployment selector.

**Pros:**
- Immediate effect — no rolling restart delay

**Cons:**
- Bypasses the Deployment controller entirely — no rollout history, no `kubectl rollout undo`
- If the Deployment is misconfigured, deleted pods may not be recreated correctly
- Selecting pods by label is fragile — label changes break targeting
- Not auditable via `kubectl rollout history`

**Verdict:** Rejected. Too low-level, breaks standard operational tooling.

---

### Option C: Trigger remediation via external script / CronJob

Run a separate Kubernetes CronJob or an out-of-cluster script that polls for firing
alerts and executes `kubectl rollout restart`.

**Pros:**
- Clean separation of concerns
- No Kubernetes SDK dependency in ai-engine

**Cons:**
- Adds operational complexity — another workload to deploy, monitor, and version
- Polling introduces latency compared to event-driven webhook trigger
- Harder to correlate remediation actions with specific alert events in logs
- Requires separate RBAC configuration and secret management

**Verdict:** Rejected. Increases operational surface without meaningful benefit.

---

### Option D: Patch `restartedAt` annotation (chosen)

Patch the deployment's `kubectl.kubernetes.io/restartedAt` annotation with the current
UTC timestamp via `AppsV1Api.patch_namespaced_deployment`. This is the exact mechanism
used by `kubectl rollout restart` internally.

**Pros:**
- Idiomatic Kubernetes — uses the same mechanism as the standard CLI command
- Triggers a rolling restart — zero downtime, respects `RollingUpdate` strategy
- Auditable: visible in `kubectl rollout history` and deployment annotations
- Single API call, no multi-step coordination required
- Easy to dry-run: skip the API call entirely when `REMEDIATION_ENABLED=false`

**Cons:**
- Rolling restart is not instantaneous — recovery takes 30–60s depending on probe config
- Does not address root causes that survive a restart (e.g. persistent volume corruption,
  misconfigured environment variables)

**Verdict:** Accepted.

---

## Decision

Implement autonomous remediation using annotation patching via the Kubernetes Python SDK
(`kubernetes==31.0.0`), encapsulated in `ai-engine/src/remediation.py`.

### Design details

**Allowlist-based targeting** (`ALERT_REMEDIATION_MAP`):

```python
ALERT_REMEDIATION_MAP = {
    "ChaosModeActive":   ("app", "victim-service"),
    "CriticalErrorRate": ("app", "victim-service"),
}
```

Only explicitly mapped alerts can trigger automated action. Unknown alerts are ignored
regardless of severity. Adding a new remediable alert requires a code change and review —
intentional friction to prevent accidental automation.

**Per-alert cooldown** (15 minutes):

```python
COOLDOWN_MINUTES = 15
```

After a successful remediation, the same alert cannot trigger another action for 15
minutes. This prevents remediation loops where a restart fails to fix the underlying
issue and alerts continue firing. Cooldown state is in-memory — resets on pod restart,
which is acceptable given the 15-minute window.

**Dry-run by default:**

```
REMEDIATION_ENABLED=false  # default in deployment.yaml
```

The feature is opt-in. In dry-run mode, the intended action is logged but no Kubernetes
API call is made. This allows safe deployment to production-like environments before
enabling live remediation.

**RBAC — least privilege:**

```yaml
rules:
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "patch"]
  resourceNames: ["victim-service"]
```

The ServiceAccount can only `patch` the specific named deployment in the `app` namespace.
It cannot list, delete, or modify any other resource.

**Audit trail:**

Every remediation attempt — successful, failed, or dry-run — is:
1. Logged as structured JSON via the ai-engine logger
2. Recorded in Prometheus: `ai_engine_remediations_total{action, status}`
3. Reported to Telegram when `status=success`
4. Attached to the GitHub Issue for the incident as a resolution comment

---

## Consequences

### Positive

- Mean time to recovery (MTTR) for chaos-mode incidents reduced from ~5 min (manual) to
  ~60s (automated rolling restart)
- On-call engineer is notified via Telegram but does not need to act for known failure modes
- Full audit trail: every action is logged, metered, and linked to the originating alert
- Safe by default: dry-run mode prevents accidental remediation during initial rollout

### Negative

- In-memory cooldown state is lost on pod restart — a crash loop could briefly allow
  back-to-back remediations. Mitigation: liveness probe prevents extended crash loops.
- Remediation scope is currently limited to `rollout restart`. Incidents requiring
  config changes, scaling, or infrastructure-level fixes still require manual intervention.
- The allowlist must be maintained manually as new alert types are added.

### Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Restart fails to fix root cause, alert re-fires | Medium | Low | 15-min cooldown limits blast radius to 4 restarts/hour |
| RBAC misconfiguration grants excess privileges | Low | High | ResourceNames scoping + `kubectl auth can-i` verified in CI |
| Remediation triggers during planned maintenance | Low | Medium | Set `REMEDIATION_ENABLED=false` before maintenance window |
| Pod restart causes brief service degradation | Low | Low | RollingUpdate strategy ensures at least 1 replica available |

---

## Implementation Notes

The feature was implemented in Week 6 of the ai-incident-response project.

```
ai-engine/
├── src/
│   ├── remediation.py          # Core logic: should_remediate(), remediate()
│   └── main.py                 # Integration: webhook handler calls remediation
├── tests/
│   └── test_remediation.py     # 9 unit tests, 100% function coverage
└── infra/k8s/ai-engine/
    └── rbac.yaml               # ServiceAccount + Role + RoleBinding
```

Test coverage includes: cooldown logic, dry-run mode, successful patch, K8s 403 error,
unknown alert rejection. All tests use mocked Kubernetes client — no cluster required.

To enable live remediation:

```bash
# Update the deployment env var
kubectl set env deployment/ai-engine \
  REMEDIATION_ENABLED=true \
  -n ai-engine

# Verify RBAC is correct
kubectl auth can-i patch deployments \
  --namespace=app \
  --as=system:serviceaccount:ai-engine:ai-engine
# Expected: yes
```

---

## References

- [kubectl rollout restart internals](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/rollout/rollout_restart.go)
- [Kubernetes Python SDK — patch_namespaced_deployment](https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/AppsV1Api.md)
- [Google SRE Book — Eliminating Toil](https://sre.google/sre-book/eliminating-toil/)
- `ai-engine/tests/test_remediation.py` — unit test suite
- `infra/k8s/ai-engine/rbac.yaml` — RBAC manifests

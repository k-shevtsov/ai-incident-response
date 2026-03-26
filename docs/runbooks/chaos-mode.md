# Runbook: Chaos Mode Incident

**Applies to:** `ChaosModeActive`, `CriticalErrorRate`, `HighErrorRate`  
**Service:** `victim-service` (namespace: `app`)  
**Last updated:** 2026-03-26

---

## What This Runbook Covers

This runbook describes how the ai-incident-response system handles chaos mode incidents,
what it does automatically, and when manual intervention is required.

---

## Automatic Response (no human action needed)

When `ChaosModeActive` or `CriticalErrorRate` fires, the system executes the following
pipeline automatically within ~60 seconds:

| Step | Action | Component |
|------|--------|-----------|
| 1 | Alert received via Alertmanager webhook | ai-engine |
| 2 | Deduplication check (30-min window per group) | ai-engine |
| 3 | Prometheus metrics queried (error rate, p95, request rate) | ai-engine |
| 4 | Recent logs fetched from Loki (last 30 lines) | ai-engine |
| 5 | AI root cause analysis generated via Claude API | ai-engine → Claude |
| 6 | GitHub Issue opened with analysis + metrics table | ai-engine → GitHub |
| 7 | Rollout restart triggered (if `REMEDIATION_ENABLED=true`) | ai-engine → k8s |
| 8 | Telegram notification sent with analysis + Issue link | ai-engine → Telegram |
| 9 | On alert resolve: Issue closed with resolution comment | ai-engine → GitHub |

**Cooldown:** after remediation, the same alert cannot trigger another restart for
15 minutes. This prevents remediation loops.

---

## Verify Automatic Remediation Worked

```bash
# Check rollout status
kubectl rollout status deployment/victim-service -n app

# Check pod restart count
kubectl get pods -n app

# Check ai-engine logs for remediation result
kubectl logs -n ai-engine deployment/ai-engine --tail=30 | grep remediation

# Check Prometheus metric
kubectl port-forward -n ai-engine svc/ai-engine 9099:80 &
curl -s http://localhost:9099/metrics/ | grep remediations_total
```

Expected log line on success:
```json
{"level": "INFO", "message": "Remediation result: {'action': 'rollout_restart', 'status': 'success', 'detail': 'app/victim-service'}"}
```

---

## Manual Intervention Required

Intervene manually in the following situations:

### 1. Remediation is in dry-run mode

Default deployment has `REMEDIATION_ENABLED=false`. The system will analyze and notify
but will not restart the pod.

**Check:**
```bash
kubectl get configmap ai-engine-config -n ai-engine -o yaml | grep REMEDIATION
```

**Enable live remediation:**
```bash
helm upgrade ai-engine ./infra/helm/ai-engine \
  --reuse-values \
  --set config.remediationEnabled=true \
  --namespace ai-engine
```

---

### 2. Restart did not fix the issue (alert re-fires after cooldown)

The root cause survives a pod restart — config issue, persistent volume, or
external dependency failure.

**Diagnose:**
```bash
# Check recent logs
kubectl logs -n app deployment/victim-service --tail=50

# Check events
kubectl get events -n app --sort-by='.lastTimestamp' | tail -20

# Check if chaos mode is still enabled
curl http://victim.local:8080/chaos/status
```

**Disable chaos mode manually:**
```bash
curl -X POST http://victim.local:8080/chaos/disable
```

---

### 3. RBAC error — remediation failed with 403

```bash
# Verify RBAC is correct
kubectl auth can-i patch deployments \
  --namespace=app \
  --as=system:serviceaccount:ai-engine:ai-engine
# If "no" — reapply RBAC

kubectl apply -f infra/k8s/ai-engine/rbac.yaml
# or via Helm:
helm upgrade ai-engine ./infra/helm/ai-engine --reuse-values --namespace ai-engine
```

---

### 4. Claude API unavailable — fallback message sent

The system falls back to raw metrics without AI analysis. Telegram receives
`⚠️ AI analysis unavailable — showing raw metrics only`.

**Check:**
```bash
kubectl logs -n ai-engine deployment/ai-engine --tail=50 | grep "Claude API"
kubectl port-forward -n ai-engine svc/ai-engine 9099:80 &
curl -s http://localhost:9099/metrics/ | grep claude_errors_total
```

**Actions:**
- Check Anthropic API status: https://status.anthropic.com
- Check API key is valid: `kubectl get secret ai-engine-secrets -n ai-engine`
- Rate limit: default 10 calls/hour — check `MAX_CLAUDE_CALLS_PER_HOUR`

---

### 5. GitHub Issue not created

```bash
kubectl logs -n ai-engine deployment/ai-engine --tail=50 | grep -i github

# Check metric
curl -s http://localhost:9099/metrics/ | grep github_issues_total
```

Common causes:
- `GITHUB_TOKEN` expired or missing `issues: write` permission
- `GITHUB_REPO` is incorrect — must be `owner/repo` format
- GitHub API rate limit exceeded (5000 req/hour for PAT)

---

## Escalation

If the incident is not resolved within **15 minutes** after the first Telegram alert:

1. Check the GitHub Issue — AI analysis may indicate a non-restartable root cause
2. Review Grafana dashboard: `http://grafana.local:8080` → AI Engine dashboard
3. Check victim-service pod logs directly:
   ```bash
   kubectl logs -n app deployment/victim-service --tail=100
   ```
4. Consider scaling up if single-pod is under load:
   ```bash
   kubectl scale deployment/victim-service -n app --replicas=2
   ```

---

## Useful Commands Summary

```bash
# Current firing alerts
kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 9093:9093 &
curl -s http://localhost:9093/api/v2/alerts | jq '[.[] | select(.status.state=="active") | .labels.alertname]'

# ai-engine health
kubectl port-forward -n ai-engine svc/ai-engine 9099:80 &
curl -s http://localhost:9099/health/ready | jq .

# Reset dedup and cooldown (forces fresh analysis on next alert)
kubectl rollout restart deployment/ai-engine -n ai-engine

# Chaos mode toggle
curl -X POST http://victim.local:8080/chaos/enable
curl -X POST http://victim.local:8080/chaos/disable
curl http://victim.local:8080/chaos/status
```

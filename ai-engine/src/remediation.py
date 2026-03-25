import logging
import os
from datetime import datetime, timedelta, timezone
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

COOLDOWN_MINUTES = 15
REMEDIATION_ENABLED = os.getenv("REMEDIATION_ENABLED", "false").lower() == "true"

ALERT_REMEDIATION_MAP = {
    "ChaosModeActive":   ("app", "victim-service"),
    "CriticalErrorRate": ("app", "victim-service"),
}

_remediation_cooldown: dict[str, datetime] = {}


def _load_k8s_client() -> client.AppsV1Api:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.AppsV1Api()


def should_remediate(alert_name: str) -> bool:
    if alert_name not in ALERT_REMEDIATION_MAP:
        return False
    last = _remediation_cooldown.get(alert_name)
    if last and datetime.now(timezone.utc) - last < timedelta(minutes=COOLDOWN_MINUTES):
        logger.info("Remediation cooldown active for %s", alert_name)
        return False
    return True


def remediate(alert_name: str) -> dict:
    namespace, deployment_name = ALERT_REMEDIATION_MAP[alert_name]

    if not REMEDIATION_ENABLED:
        logger.info(
            "[DRY-RUN] Would restart %s/%s for alert %s",
            namespace, deployment_name, alert_name
        )
        return {
            "action": "rollout_restart",
            "status": "dry_run",
            "detail": f"{namespace}/{deployment_name}",
        }

    try:
        apps_v1 = _load_k8s_client()
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            }
        }
        apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch,
        )
        _remediation_cooldown[alert_name] = datetime.now(timezone.utc)
        logger.info("Restarted %s/%s for alert %s", namespace, deployment_name, alert_name)
        return {
            "action": "rollout_restart",
            "status": "success",
            "detail": f"{namespace}/{deployment_name}",
        }

    except ApiException as e:
        logger.error("K8s API error during remediation: status=%s reason=%s", e.status, e.reason)
        return {
            "action": "rollout_restart",
            "status": "failed",
            "detail": str(e.reason),
        }

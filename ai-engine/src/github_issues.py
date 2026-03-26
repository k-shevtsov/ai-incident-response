"""
github_issues.py — автоматическое создание и закрытие GitHub Issues при инцидентах.

Один Issue на инцидент. Dedup по issue_key = "service:primary_name" — стабильный
идентификатор инцидента, не зависящий от состава группы алертов в конкретный момент.
group_key используется только для логов. При resolved — Issue закрывается с комментарием.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")  # формат: "owner/repo"
GITHUB_API_URL = "https://api.github.com"

# issue_key ("service:primary_name") → issue_number
# Стабильный ключ: не меняется при изменении состава группы алертов.
_open_issues: dict[str, int] = {}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _is_configured() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


async def create_issue(
    issue_key: str,
    group_key: str,
    alert_names: list[str],
    primary_name: str,
    service: str,
    severity: str,
    analysis: str | None,
    metrics: dict,
    timestamp: str,
) -> int | None:
    """
    Создаёт GitHub Issue для инцидента.
    Возвращает номер Issue или None при ошибке / не сконфигурировано.

    issue_key = "service:primary_name" — стабильный dedup-ключ.
    group_key = "service:alert1:alert2:..." — меняется при изменении состава группы,
    используется только для логов.
    """
    if not _is_configured():
        logger.warning("GitHub Issues not configured (GITHUB_TOKEN or GITHUB_REPO missing)")
        return None

    if issue_key in _open_issues:
        logger.info(
            "Issue already open for %s (group %s): #%d",
            issue_key, group_key, _open_issues[issue_key],
        )
        return _open_issues[issue_key]

    title = _build_title(primary_name, alert_names, service)
    body = _build_body(alert_names, primary_name, service, severity, analysis, metrics, timestamp)
    labels = _build_labels(severity, alert_names)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues",
                headers=_headers(),
                json={"title": title, "body": body, "labels": labels},
                timeout=10,
            )
            if resp.status_code == 201:
                issue_number = resp.json()["number"]
                _open_issues[issue_key] = issue_number
                logger.info(
                    "Created GitHub Issue #%d for %s (group %s)",
                    issue_number, issue_key, group_key,
                )
                return issue_number
            else:
                logger.error(
                    "GitHub API error creating issue: status=%d body=%s",
                    resp.status_code, resp.text[:200],
                )
                return None
    except Exception as e:
        logger.error("Failed to create GitHub Issue: %s", e)
        return None


async def close_issue(
    issue_key: str,
    resolved_at: str,
    remediation_result: dict | None = None,
) -> bool:
    """
    Закрывает GitHub Issue для issue_key с комментарием о резолюции.
    Возвращает True если успешно закрыт.
    """
    if not _is_configured():
        return False

    issue_number = _open_issues.get(issue_key)
    if issue_number is None:
        logger.info("No open Issue found for %s, skipping close", issue_key)
        return False

    comment = _build_resolution_comment(resolved_at, remediation_result)

    try:
        async with httpx.AsyncClient() as client:
            comment_resp = await client.post(
                f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues/{issue_number}/comments",
                headers=_headers(),
                json={"body": comment},
                timeout=10,
            )
            if comment_resp.status_code != 201:
                logger.warning(
                    "Failed to add resolution comment to #%d: status=%d",
                    issue_number, comment_resp.status_code,
                )

            close_resp = await client.patch(
                f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues/{issue_number}",
                headers=_headers(),
                json={"state": "closed"},
                timeout=10,
            )
            if close_resp.status_code == 200:
                logger.info("Closed GitHub Issue #%d for %s", issue_number, issue_key)
                _open_issues.pop(issue_key, None)
                return True
            else:
                logger.error(
                    "GitHub API error closing issue #%d: status=%d body=%s",
                    issue_number, close_resp.status_code, close_resp.text[:200],
                )
                return False
    except Exception as e:
        logger.error("Failed to close GitHub Issue #%d: %s", issue_number, e)
        return False


# ---------------------------------------------------------------------------
# Внутренние хелперы — форматирование
# ---------------------------------------------------------------------------

def _build_title(primary_name: str, alert_names: list[str], service: str) -> str:
    if len(alert_names) > 1:
        others_count = len(alert_names) - 1
        return f"[Incident] {primary_name} (+{others_count} more) — {service}"
    return f"[Incident] {primary_name} — {service}"


def _build_labels(severity: str, alert_names: list[str]) -> list[str]:
    labels = ["incident", "auto-generated"]
    if severity in ("critical", "warning"):
        labels.append(severity)
    if "ChaosModeActive" in alert_names:
        labels.append("chaos")
    return labels


def _build_body(
    alert_names: list[str],
    primary_name: str,
    service: str,
    severity: str,
    analysis: str | None,
    metrics: dict,
    timestamp: str,
) -> str:
    error_rate = metrics.get("error_rate")
    request_rate = metrics.get("request_rate")
    p95 = metrics.get("p95_latency")
    chaos = metrics.get("chaos_mode")

    alerts_list = "\n".join(f"- `{a}`" for a in alert_names)

    error_rate_str = f"{error_rate:.1f}%" if error_rate is not None else "N/A"
    request_rate_str = f"{request_rate:.3f} req/s" if request_rate is not None else "N/A"
    p95_str = f"{p95:.3f}s" if p95 is not None else "N/A"

    metrics_section = (
        "| Metric | Value |\n"
        "|--------|-------|\n"
        f"| Error rate | {error_rate_str} |\n"
        f"| Request rate | {request_rate_str} |\n"
        f"| P95 latency | {p95_str} |\n"
        f"| Chaos mode | {chaos} |\n"
    )

    analysis_section = (
        f"### 🧠 AI Analysis\n\n{analysis}"
        if analysis
        else "### 🧠 AI Analysis\n\n⚠️ AI analysis unavailable — raw metrics only."
    )

    header = (
        "## 🚨 Incident Report\n\n"
        f"**Service:** `{service}`  \n"
        f"**Primary alert:** `{primary_name}`  \n"
        f"**Severity:** `{severity}`  \n"
        f"**Detected at:** {timestamp}\n\n"
        "### 🔔 Firing Alerts\n\n"
        f"{alerts_list}\n\n"
        "### 📊 Metrics at Detection Time\n\n"
    )
    footer = (
        "\n\n---\n"
        f"*Auto-generated by [ai-engine](https://github.com/{GITHUB_REPO}). "
        "Do not edit — this issue is managed automatically.*"
    )

    return header + metrics_section + "\n" + analysis_section + footer


def _build_resolution_comment(
    resolved_at: str,
    remediation_result: dict | None,
) -> str:
    lines = [
        "## ✅ Incident Resolved",
        "",
        f"**Resolved at:** {resolved_at}",
        "",
    ]

    if remediation_result:
        status = remediation_result.get("status", "unknown")
        action = remediation_result.get("action", "unknown")
        detail = remediation_result.get("detail", "")
        emoji = {"success": "✅", "dry_run": "🔍", "failed": "❌"}.get(status, "❓")
        lines += [
            "### 🔧 Auto-Remediation",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Action | `{action}` |",
            f"| Status | {emoji} `{status}` |",
            f"| Target | `{detail}` |",
            "",
        ]
    else:
        lines += ["*No auto-remediation was triggered for this incident.*", ""]

    lines += [
        "---",
        "*Closed automatically by ai-engine upon alert resolution.*",
    ]

    return "\n".join(lines)

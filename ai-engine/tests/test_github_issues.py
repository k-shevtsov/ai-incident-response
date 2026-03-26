"""
tests/test_github_issues.py — unit tests for github_issues module.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from github_issues import (
    create_issue,
    close_issue,
    _open_issues,
    _build_title,
    _build_labels,
    _build_resolution_comment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_METRICS = {
    "error_rate": 45.3,
    "request_rate": 1.234,
    "p95_latency": 2.501,
    "chaos_mode": 1.0,
}

DEFAULT_CREATE_KWARGS = dict(
    issue_key="victim-service:ChaosModeActive",
    group_key="victim-service:ChaosModeActive",
    alert_names=["ChaosModeActive"],
    primary_name="ChaosModeActive",
    service="victim-service",
    severity="critical",
    analysis="Root cause: chaos mode injected faults.",
    metrics=SAMPLE_METRICS,
    timestamp="12:00 UTC",
)


def setup_function():
    """Clear shared state before every test."""
    _open_issues.clear()


# ---------------------------------------------------------------------------
# _build_title
# ---------------------------------------------------------------------------

def test_build_title_single_alert():
    title = _build_title("ChaosModeActive", ["ChaosModeActive"], "victim-service")
    assert title == "[Incident] ChaosModeActive — victim-service"


def test_build_title_multiple_alerts():
    title = _build_title("ChaosModeActive", ["ChaosModeActive", "CriticalErrorRate"], "victim-service")
    assert title == "[Incident] ChaosModeActive (+1 more) — victim-service"


# ---------------------------------------------------------------------------
# _build_labels
# ---------------------------------------------------------------------------

def test_labels_include_incident_and_auto_generated():
    labels = _build_labels("critical", ["ChaosModeActive"])
    assert "incident" in labels
    assert "auto-generated" in labels


def test_labels_include_severity_critical():
    labels = _build_labels("critical", ["ChaosModeActive"])
    assert "critical" in labels


def test_labels_include_chaos_tag():
    labels = _build_labels("critical", ["ChaosModeActive"])
    assert "chaos" in labels


def test_labels_no_chaos_tag_for_other_alerts():
    labels = _build_labels("warning", ["HighErrorRate"])
    assert "chaos" not in labels


# ---------------------------------------------------------------------------
# _build_resolution_comment
# ---------------------------------------------------------------------------

def test_resolution_comment_contains_resolved_at():
    comment = _build_resolution_comment("2026-01-01 12:00 UTC", None)
    assert "2026-01-01 12:00 UTC" in comment


def test_resolution_comment_with_remediation():
    result = {"action": "rollout_restart", "status": "success", "detail": "app/victim-service"}
    comment = _build_resolution_comment("2026-01-01 12:00 UTC", result)
    assert "rollout_restart" in comment
    assert "success" in comment
    assert "app/victim-service" in comment


def test_resolution_comment_without_remediation():
    comment = _build_resolution_comment("2026-01-01 12:00 UTC", None)
    assert "No auto-remediation" in comment


# ---------------------------------------------------------------------------
# create_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_issue_not_configured_returns_none():
    with patch("github_issues.GITHUB_TOKEN", ""), patch("github_issues.GITHUB_REPO", ""):
        result = await create_issue(**DEFAULT_CREATE_KWARGS)
    assert result is None


@pytest.mark.asyncio
async def test_create_issue_success():
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"number": 42}

    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await create_issue(**DEFAULT_CREATE_KWARGS)

    assert result == 42
    assert _open_issues["victim-service:ChaosModeActive"] == 42


@pytest.mark.asyncio
async def test_create_issue_dedup_same_primary_different_group():
    """
    Один и тот же primary alert (issue_key) но разный состав группы (group_key)
    не должен создавать второй Issue — dedup по issue_key.
    """
    _open_issues["victim-service:ChaosModeActive"] = 55

    kwargs_different_group = {**DEFAULT_CREATE_KWARGS, "group_key": "victim-service:ChaosModeActive:CriticalErrorRate:HighErrorRate"}

    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        result = await create_issue(**kwargs_different_group)

    mock_client.post.assert_not_called()
    assert result == 55


    _open_issues["victim-service:ChaosModeActive"] = 99

    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        result = await create_issue(**DEFAULT_CREATE_KWARGS)

    # API не должен вызываться
    mock_client.post.assert_not_called()
    assert result == 99


@pytest.mark.asyncio
async def test_create_issue_api_error_returns_none():
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"

    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await create_issue(**DEFAULT_CREATE_KWARGS)

    assert result is None
    assert "victim-service:ChaosModeActive" not in _open_issues


@pytest.mark.asyncio
async def test_create_issue_network_exception_returns_none():
    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        mock_client_cls.return_value = mock_client

        result = await create_issue(**DEFAULT_CREATE_KWARGS)

    assert result is None


# ---------------------------------------------------------------------------
# close_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_issue_no_open_issue_returns_false():
    result = await close_issue("victim-service:ChaosModeActive", "2026-01-01 12:00 UTC")
    assert result is False


@pytest.mark.asyncio
async def test_close_issue_success():
    _open_issues["victim-service:ChaosModeActive"] = 42

    mock_comment_resp = MagicMock()
    mock_comment_resp.status_code = 201

    mock_close_resp = MagicMock()
    mock_close_resp.status_code = 200
    mock_close_resp.json.return_value = {"number": 42, "state": "closed"}

    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_comment_resp)
        mock_client.patch = AsyncMock(return_value=mock_close_resp)
        mock_client_cls.return_value = mock_client

        result = await close_issue(
            "victim-service:ChaosModeActive",
            "2026-01-01 12:00 UTC",
            {"action": "rollout_restart", "status": "success", "detail": "app/victim-service"},
        )

    assert result is True
    assert "victim-service:ChaosModeActive" not in _open_issues


@pytest.mark.asyncio
async def test_close_issue_removes_from_open_issues_on_success():
    _open_issues["victim-service:ChaosModeActive"] = 7

    mock_comment_resp = MagicMock()
    mock_comment_resp.status_code = 201
    mock_close_resp = MagicMock()
    mock_close_resp.status_code = 200

    with patch("github_issues.GITHUB_TOKEN", "ghp_test"), \
         patch("github_issues.GITHUB_REPO", "owner/repo"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_comment_resp)
        mock_client.patch = AsyncMock(return_value=mock_close_resp)
        mock_client_cls.return_value = mock_client

        await close_issue("victim-service:ChaosModeActive", "2026-01-01 12:00 UTC")

    assert "victim-service:ChaosModeActive" not in _open_issues


@pytest.mark.asyncio
async def test_close_issue_not_configured_returns_false():
    _open_issues["victim-service:ChaosModeActive"] = 42
    with patch("github_issues.GITHUB_TOKEN", ""), patch("github_issues.GITHUB_REPO", ""):
        result = await close_issue("victim-service:ChaosModeActive", "2026-01-01 12:00 UTC")
    assert result is False

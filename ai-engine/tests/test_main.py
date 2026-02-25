import pytest
import html
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from freezegun import freeze_time

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from pydantic import ValidationError
from main import (
    should_analyze,
    pick_primary_alert,
    format_message,
    _last_analyzed,
    DEDUP_WINDOW_MINUTES,
    Alert, AlertLabel, AlertAnnotation,
    WebhookPayload,
    app,
)
from fastapi.testclient import TestClient

client = TestClient(app)


def make_alert(alertname: str, status: str = "firing", service: str = "victim-service") -> Alert:
    return Alert(
        status=status,
        labels=AlertLabel(alertname=alertname, service=service),
        annotations=AlertAnnotation(summary="test", description="test"),
    )


SAMPLE_PAYLOAD = {
    "version": "4",
    "status": "firing",
    "receiver": "ai-engine",
    "groupLabels": {"service": "victim-service"},
    "commonLabels": {"service": "victim-service"},
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "HighErrorRate", "service": "victim-service", "severity": "critical"},
            "annotations": {"summary": "High error rate", "description": "Error rate above threshold"},
            "startsAt": "2026-02-25T17:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "",
        }
    ],
}


# --- Deduplication tests ---

class TestShouldAnalyze:
    def setup_method(self):
        _last_analyzed.clear()

    @freeze_time("2026-02-25 12:00:00")
    def test_new_group_should_analyze(self):
        assert should_analyze("victim-service:HighErrorRate") is True

    @freeze_time("2026-02-25 12:00:00")
    def test_recent_group_should_not_analyze(self):
        key = "victim-service:HighErrorRate"
        _last_analyzed[key] = datetime.now(timezone.utc)
        assert should_analyze(key) is False

    @freeze_time("2026-02-25 12:00:00")
    def test_old_group_should_analyze(self):
        key = "victim-service:HighErrorRate"
        _last_analyzed[key] = datetime.now(timezone.utc) - timedelta(minutes=DEDUP_WINDOW_MINUTES + 1)
        assert should_analyze(key) is True

    @freeze_time("2026-02-25 12:00:00")
    def test_exactly_at_window_boundary_should_not_analyze(self):
        key = "victim-service:HighErrorRate"
        _last_analyzed[key] = datetime.now(timezone.utc) - timedelta(minutes=DEDUP_WINDOW_MINUTES)
        assert should_analyze(key) is False

    @freeze_time("2026-02-25 12:00:00")
    def test_different_groups_are_independent(self):
        key1 = "victim-service:HighErrorRate"
        key2 = "victim-service:CriticalErrorRate"
        _last_analyzed[key1] = datetime.now(timezone.utc)
        assert should_analyze(key1) is False
        assert should_analyze(key2) is True


# --- Primary alert selection tests ---

class TestPickPrimaryAlert:
    def test_picks_highest_priority(self):
        alerts = [
            make_alert("HighErrorRate"),
            make_alert("ChaosModeActive"),
            make_alert("CriticalErrorRate"),
        ]
        assert pick_primary_alert(alerts).labels.alertname == "ChaosModeActive"

    def test_picks_critical_over_high(self):
        alerts = [make_alert("HighErrorRate"), make_alert("CriticalErrorRate")]
        assert pick_primary_alert(alerts).labels.alertname == "CriticalErrorRate"

    def test_unknown_alert_is_lowest_priority(self):
        alerts = [make_alert("UnknownAlert"), make_alert("SlowResponseTime")]
        assert pick_primary_alert(alerts).labels.alertname == "SlowResponseTime"

    def test_skips_resolved_picks_firing(self):
        alerts = [
            make_alert("ChaosModeActive", status="resolved"),
            make_alert("HighErrorRate", status="firing"),
        ]
        assert pick_primary_alert(alerts).labels.alertname == "HighErrorRate"

    def test_all_resolved_returns_none(self):
        alerts = [
            make_alert("HighErrorRate", status="resolved"),
            make_alert("CriticalErrorRate", status="resolved"),
        ]
        assert pick_primary_alert(alerts) is None

    def test_single_alert_returns_it(self):
        alerts = [make_alert("HighErrorRate")]
        assert pick_primary_alert(alerts).labels.alertname == "HighErrorRate"

    def test_empty_list_returns_none(self):
        assert pick_primary_alert([]) is None


# --- Webhook payload parsing tests ---

class TestWebhookPayload:
    def test_valid_payload(self):
        payload = WebhookPayload(**{"alerts": [
            {"status": "firing", "labels": {"alertname": "HighErrorRate", "service": "victim-service", "severity": "critical"},
             "annotations": {"summary": "s", "description": "d"}}
        ]})
        assert payload.alerts[0].labels.alertname == "HighErrorRate"

    def test_empty_alerts(self):
        assert WebhookPayload(alerts=[]).alerts == []

    def test_missing_optional_fields_use_defaults(self):
        payload = WebhookPayload(**{"alerts": [{"status": "firing", "labels": {"alertname": "X"}}]})
        assert payload.alerts[0].labels.service == "unknown"
        assert payload.alerts[0].annotations.summary == ""

    def test_invalid_payload_raises_validation_error(self):
        with pytest.raises(ValidationError):
            WebhookPayload(alerts=[{"wrong": "format"}])

    def test_mixed_statuses(self):
        payload = WebhookPayload(**{"alerts": [
            {"status": "firing", "labels": {"alertname": "A"}},
            {"status": "resolved", "labels": {"alertname": "B"}},
        ]})
        firing = [a for a in payload.alerts if a.status == "firing"]
        assert len(firing) == 1 and firing[0].labels.alertname == "A"


# --- format_message tests ---

class TestFormatMessage:
    def setup_method(self):
        self.metrics = {"error_rate": 100.0, "request_rate": 0.122, "p95_latency": 1.938, "chaos_mode": 1.0}

    def test_contains_alert_name(self):
        msg = format_message(["HighErrorRate"], "HighErrorRate", self.metrics, "analysis text", "12:00 UTC")
        assert "HighErrorRate" in msg

    def test_contains_metrics(self):
        msg = format_message(["HighErrorRate"], "HighErrorRate", self.metrics, "analysis", "12:00 UTC")
        assert "100.0%" in msg
        assert "1.938s" in msg

    def test_html_escapes_analysis(self):
        analysis = "<b>Use kubectl</b> & check pods"
        msg = format_message(["HighErrorRate"], "HighErrorRate", self.metrics, analysis, "12:00 UTC")
        assert "<b>Use kubectl</b>" not in msg
        assert "&lt;b&gt;" in msg
        assert "&amp;" in msg

    def test_html_escapes_alert_name(self):
        msg = format_message(["<script>xss</script>"], "<script>xss</script>", self.metrics, "ok", "12:00 UTC")
        assert "<script>" not in msg

    def test_multiple_alerts_shows_others(self):
        alert_names = ["ChaosModeActive", "CriticalErrorRate", "HighErrorRate"]
        msg = format_message(alert_names, "ChaosModeActive", self.metrics, "analysis", "12:00 UTC")
        assert "CriticalErrorRate" in msg
        assert "HighErrorRate" in msg

    def test_none_metrics_shows_na(self):
        metrics = {"error_rate": None, "request_rate": None, "p95_latency": None, "chaos_mode": None}
        msg = format_message(["HighErrorRate"], "HighErrorRate", metrics, "analysis", "12:00 UTC")
        assert "N/A" in msg


# --- Endpoint integration tests ---

class TestWebhookEndpoint:
    def setup_method(self):
        _last_analyzed.clear()

    def test_health_endpoint(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_no_firing_alerts_skipped(self):
        payload = {"alerts": [{"status": "resolved", "labels": {"alertname": "HighErrorRate", "service": "victim-service"}}]}
        resp = client.post("/webhook", json=payload)
        assert resp.status_code == 200
        assert resp.json()["skipped"] == "no firing alerts"

    def test_invalid_payload_returns_error(self):
        resp = client.post("/webhook", json={"alerts": [{"wrong": "format"}]})
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_duplicate_group_skipped(self):
        key = "victim-service:HighErrorRate"
        _last_analyzed[key] = datetime.now(timezone.utc)
        resp = client.post("/webhook", json=SAMPLE_PAYLOAD)
        assert resp.status_code == 200
        assert "deduplicated" in resp.json()["skipped"]

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="some log lines")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="Root cause: chaos mode active.")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_full_pipeline_calls_all_steps(self, mock_telegram, mock_claude, mock_logs, mock_metrics):
        resp = client.post("/webhook", json=SAMPLE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_metrics.assert_called_once()
        mock_logs.assert_called_once()
        mock_claude.assert_called_once()
        mock_telegram.assert_called_once()

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_full_pipeline_telegram_receives_html_safe_message(self, mock_telegram, mock_claude, mock_logs, mock_metrics):
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        call_args = mock_telegram.call_args[0][0]
        assert "<b>AI Incident Analysis</b>" in call_args
        assert "<code>" in call_args


# --- Additional tests from review ---

class TestPickPrimaryAlertAdditional:
    def test_no_alerts_returns_none(self):
        """Явно фиксируем: пустой список → None"""
        assert pick_primary_alert([]) is None

    def test_firing_wins_over_resolved_regardless_of_priority(self):
        """HighErrorRate firing должен выиграть у CriticalErrorRate resolved"""
        alerts = [
            make_alert("CriticalErrorRate", status="resolved"),
            make_alert("HighErrorRate", status="firing"),
        ]
        assert pick_primary_alert(alerts).labels.alertname == "HighErrorRate"


class TestWebhookDedupBehavior:
    def setup_method(self):
        _last_analyzed.clear()

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_last_analyzed_updated_after_processing(self, mock_tg, mock_claude, mock_logs, mock_metrics):
        """После успешного вызова _last_analyzed должен содержать ключ группы"""
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        key = "victim-service:HighErrorRate"
        assert key in _last_analyzed
        assert isinstance(_last_analyzed[key], datetime)

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_second_call_within_window_is_deduplicated(self, mock_tg, mock_claude, mock_logs, mock_metrics):
        """Два вызова подряд — Claude и Telegram должны вызваться только один раз"""
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        mock_claude.assert_called_once()
        mock_tg.assert_called_once()

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_second_call_returns_deduplicated_status(self, mock_tg, mock_claude, mock_logs, mock_metrics):
        """Второй вызов явно возвращает skipped с причиной"""
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        resp = client.post("/webhook", json=SAMPLE_PAYLOAD)
        assert "deduplicated" in resp.json()["skipped"]

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
    format_fallback_message,
    _last_analyzed,
    DEDUP_WINDOW_MINUTES,
    CLAUDE_RETRY_DELAYS,
    Alert, AlertLabel, AlertAnnotation,
    WebhookPayload,
    app,
    alerts_received_total,
    alerts_analyzed_total,
    claude_errors_total,
    telegram_errors_total,
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


# --- format_fallback_message tests ---

class TestFormatFallbackMessage:
    def setup_method(self):
        self.metrics = {"error_rate": 55.0, "request_rate": 0.5, "p95_latency": 2.1, "chaos_mode": 1.0}

    def test_contains_warning_emoji(self):
        msg = format_fallback_message(["HighErrorRate"], "HighErrorRate", self.metrics, "12:00 UTC")
        assert "⚠️" in msg

    def test_contains_raw_metrics_only_text(self):
        msg = format_fallback_message(["HighErrorRate"], "HighErrorRate", self.metrics, "12:00 UTC")
        assert "raw metrics only" in msg

    def test_does_not_contain_ai_analysis_header(self):
        msg = format_fallback_message(["HighErrorRate"], "HighErrorRate", self.metrics, "12:00 UTC")
        assert "AI Incident Analysis" not in msg

    def test_contains_alert_name(self):
        msg = format_fallback_message(["CriticalErrorRate"], "CriticalErrorRate", self.metrics, "12:00 UTC")
        assert "CriticalErrorRate" in msg

    def test_contains_metrics_values(self):
        msg = format_fallback_message(["HighErrorRate"], "HighErrorRate", self.metrics, "12:00 UTC")
        assert "55.0%" in msg
        assert "2.100s" in msg

    def test_multiple_alerts(self):
        msg = format_fallback_message(
            ["ChaosModeActive", "HighErrorRate"], "ChaosModeActive", self.metrics, "12:00 UTC"
        )
        assert "HighErrorRate" in msg

    def test_none_metrics_shows_na(self):
        metrics = {"error_rate": None, "request_rate": None, "p95_latency": None, "chaos_mode": None}
        msg = format_fallback_message(["HighErrorRate"], "HighErrorRate", metrics, "12:00 UTC")
        assert "N/A" in msg


# --- analyze_with_claude retry tests ---

class TestAnalyzeWithClaudeRetry:
    @pytest.mark.asyncio
    @patch("main.asyncio.sleep", new_callable=AsyncMock)
    @patch("main.anthropic.Anthropic")
    async def test_success_on_first_attempt(self, mock_anthropic_cls, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Root cause: chaos.")]
        mock_client.messages.create.return_value = mock_msg

        from main import analyze_with_claude
        result = await analyze_with_claude(["HighErrorRate"], {}, "logs")

        assert result == "Root cause: chaos."
        mock_client.messages.create.assert_called_once()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("main.asyncio.sleep", new_callable=AsyncMock)
    @patch("main.anthropic.Anthropic")
    async def test_retries_on_failure_then_succeeds(self, mock_anthropic_cls, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Analysis after retry.")]
        mock_client.messages.create.side_effect = [
            Exception("timeout"),
            mock_msg,
        ]

        from main import analyze_with_claude
        result = await analyze_with_claude(["HighErrorRate"], {}, "logs")

        assert result == "Analysis after retry."
        assert mock_client.messages.create.call_count == 2
        mock_sleep.assert_called_once_with(CLAUDE_RETRY_DELAYS[0])

    @pytest.mark.asyncio
    @patch("main.asyncio.sleep", new_callable=AsyncMock)
    @patch("main.anthropic.Anthropic")
    async def test_returns_none_after_all_retries_exhausted(self, mock_anthropic_cls, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API down")

        from main import analyze_with_claude
        result = await analyze_with_claude(["HighErrorRate"], {}, "logs")

        assert result is None
        assert mock_client.messages.create.call_count == len(CLAUDE_RETRY_DELAYS)

    @pytest.mark.asyncio
    @patch("main.asyncio.sleep", new_callable=AsyncMock)
    @patch("main.anthropic.Anthropic")
    async def test_sleep_delays_match_config(self, mock_anthropic_cls, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("fail")

        from main import analyze_with_claude
        await analyze_with_claude(["HighErrorRate"], {}, "logs")

        # Last attempt has no sleep after it — delays between attempts only
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == CLAUDE_RETRY_DELAYS[:-1]

    @pytest.mark.asyncio
    @patch("main.asyncio.sleep", new_callable=AsyncMock)
    @patch("main.anthropic.Anthropic")
    async def test_claude_errors_counter_incremented_on_exhaustion(self, mock_anthropic_cls, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("fail")

        before = claude_errors_total._value.get()
        from main import analyze_with_claude
        await analyze_with_claude(["HighErrorRate"], {}, "logs")
        after = claude_errors_total._value.get()

        assert after == before + 1

    @pytest.mark.asyncio
    @patch("main.asyncio.sleep", new_callable=AsyncMock)
    @patch("main.anthropic.Anthropic")
    async def test_claude_errors_counter_not_incremented_on_success(self, mock_anthropic_cls, mock_sleep):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_msg

        before = claude_errors_total._value.get()
        from main import analyze_with_claude
        await analyze_with_claude(["HighErrorRate"], {}, "logs")
        after = claude_errors_total._value.get()

        assert after == before


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
        assert resp.json()["skipped"] == "resolved alerts processed"
        
    def test_empty_alerts_skipped(self):
        payload = {"alerts": []}
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

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value=None)
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_fallback_message_sent_when_claude_unavailable(self, mock_telegram, mock_claude, mock_logs, mock_metrics):
        """When analyze_with_claude returns None, fallback message is sent instead of AI analysis."""
        resp = client.post("/webhook", json=SAMPLE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        call_args = mock_telegram.call_args[0][0]
        assert "raw metrics only" in call_args
        assert "AI analysis unavailable" in call_args

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value=None)
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_fallback_message_does_not_contain_ai_analysis_header(self, mock_telegram, mock_claude, mock_logs, mock_metrics):
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        call_args = mock_telegram.call_args[0][0]
        assert "AI Incident Analysis" not in call_args

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value=None)
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_alerts_analyzed_counter_not_incremented_on_fallback(self, mock_telegram, mock_claude, mock_logs, mock_metrics):
        before = alerts_analyzed_total._value.get()
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        after = alerts_analyzed_total._value.get()
        assert after == before

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis ok")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_alerts_analyzed_counter_incremented_on_success(self, mock_telegram, mock_claude, mock_logs, mock_metrics):
        before = alerts_analyzed_total._value.get()
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        after = alerts_analyzed_total._value.get()
        assert after == before + 1

    def test_metrics_endpoint_returns_200(self):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_endpoint_contains_custom_metrics(self):
        resp = client.get("/metrics")
        body = resp.text
        assert "ai_engine_alerts_received_total" in body
        assert "ai_engine_alerts_analyzed_total" in body
        assert "ai_engine_claude_errors_total" in body
        assert "ai_engine_telegram_errors_total" in body
        assert "ai_engine_claude_request_duration_seconds" in body


# --- Additional tests from review ---

class TestPickPrimaryAlertAdditional:
    def test_no_alerts_returns_none(self):
        assert pick_primary_alert([]) is None

    def test_firing_wins_over_resolved_regardless_of_priority(self):
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
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        key = "victim-service:HighErrorRate"
        assert key in _last_analyzed
        assert isinstance(_last_analyzed[key], datetime)

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_second_call_within_window_is_deduplicated(self, mock_tg, mock_claude, mock_logs, mock_metrics):
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        mock_claude.assert_called_once()
        mock_tg.assert_called_once()

    @patch("main.get_metrics", new_callable=AsyncMock, return_value={"error_rate": 100.0, "request_rate": 0.1, "p95_latency": 1.5, "chaos_mode": 1.0})
    @patch("main.get_logs", new_callable=AsyncMock, return_value="logs")
    @patch("main.analyze_with_claude", new_callable=AsyncMock, return_value="analysis")
    @patch("main.send_telegram", new_callable=AsyncMock)
    def test_second_call_returns_deduplicated_status(self, mock_tg, mock_claude, mock_logs, mock_metrics):
        client.post("/webhook", json=SAMPLE_PAYLOAD)
        resp = client.post("/webhook", json=SAMPLE_PAYLOAD)
        assert "deduplicated" in resp.json()["skipped"]

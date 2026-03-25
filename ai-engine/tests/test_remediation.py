from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from freezegun import freeze_time
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from remediation import should_remediate, remediate, _remediation_cooldown


def setup_function():
    _remediation_cooldown.clear()


def test_unknown_alert_not_remediated():
    assert should_remediate("UnknownAlert") is False


def test_known_alert_can_be_remediated():
    assert should_remediate("ChaosModeActive") is True


def test_cooldown_blocks_second_call():
    _remediation_cooldown["ChaosModeActive"] = datetime.now(timezone.utc)
    assert should_remediate("ChaosModeActive") is False


@freeze_time("2026-01-01 12:00:00")
def test_cooldown_expires_after_15_min():
    _remediation_cooldown["ChaosModeActive"] = datetime(2026, 1, 1, 11, 44, 0, tzinfo=timezone.utc)
    assert should_remediate("ChaosModeActive") is True


@freeze_time("2026-01-01 12:00:00")
def test_cooldown_still_active_at_14_min():
    _remediation_cooldown["ChaosModeActive"] = datetime(2026, 1, 1, 11, 46, 0, tzinfo=timezone.utc)
    assert should_remediate("ChaosModeActive") is False


def test_dry_run_does_not_call_k8s():
    with patch("remediation.REMEDIATION_ENABLED", False):
        with patch("remediation._load_k8s_client") as mock_k8s:
            result = remediate("ChaosModeActive")
            mock_k8s.assert_not_called()
            assert result["status"] == "dry_run"
            assert result["action"] == "rollout_restart"
            assert result["detail"] == "app/victim-service"


def test_remediation_success():
    mock_api = MagicMock()
    with patch("remediation.REMEDIATION_ENABLED", True):
        with patch("remediation._load_k8s_client", return_value=mock_api):
            result = remediate("ChaosModeActive")
            mock_api.patch_namespaced_deployment.assert_called_once()
            assert result["status"] == "success"
            assert result["detail"] == "app/victim-service"


def test_remediation_sets_cooldown():
    mock_api = MagicMock()
    with patch("remediation.REMEDIATION_ENABLED", True):
        with patch("remediation._load_k8s_client", return_value=mock_api):
            remediate("ChaosModeActive")
            assert "ChaosModeActive" in _remediation_cooldown


def test_remediation_k8s_403():
    from kubernetes.client.exceptions import ApiException
    mock_api = MagicMock()
    mock_api.patch_namespaced_deployment.side_effect = ApiException(status=403)
    with patch("remediation.REMEDIATION_ENABLED", True):
        with patch("remediation._load_k8s_client", return_value=mock_api):
            result = remediate("ChaosModeActive")
            assert result["status"] == "failed"
            assert result["action"] == "rollout_restart"

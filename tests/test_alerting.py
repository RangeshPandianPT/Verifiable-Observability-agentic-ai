import pytest
from unittest.mock import patch, MagicMock

from verifiable_observability.core.alerting import WebhookAlerter, CompositeAlerter
from verifiable_observability.storage.models import (
    Trajectory,
    Task,
    Domain,
    StrategyProfile,
    RiskTier,
    TrajectoryOutcome,
)
from verifiable_observability.core.metrics import DriftReport, TrendDirection


@pytest.fixture
def dummy_trajectory():
    return Trajectory(
        trajectory_id="traj-123",
        task=Task(
            task_id="task-456",
            domain=Domain.FINANCE,
            description="Transfer $1000",
            metadata={},
        ),
        strategy_profile=StrategyProfile(
            task_id="task-456",
            domain=Domain.FINANCE,
            task_type="transfer",
            risk_tier=RiskTier.LOW,
            confidence=0.9,
            expected_turn_range=(1, 5),
            active_constraint_set_id="fin-constraints-001",
        ),
        outcome=TrajectoryOutcome.BLOCKED,
        failure_reason="CCM Blocked",
        agent_backend="ollama",
        model_name="llama3",
    )


@pytest.fixture
def dummy_drift_report():
    return DriftReport(
        trajectory_id="traj-123",
        num_turns=5,
        drift_detected=True,
        drift_reasons=["RCR declining: slope=-0.150 < -0.10"],
        rcr_trend=TrendDirection.DEGRADING,
        ccr_trend=TrendDirection.STABLE,
    )


@patch("urllib.request.urlopen")
def test_webhook_alerter_block(mock_urlopen, dummy_trajectory):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    alerter = WebhookAlerter(webhook_url="http://example.com/webhook")
    alerter.notify_block(dummy_trajectory)

    # Verify that urlopen was called
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "http://example.com/webhook"
    assert req.method == "POST"
    
    # Verify payload
    import json
    data = json.loads(req.data.decode("utf-8"))
    assert "text" in data
    assert "AGENT BLOCKED" in data["text"]
    assert "traj-123" in data["text"]
    assert "CCM Blocked" in data["text"]


@patch("urllib.request.urlopen")
def test_webhook_alerter_drift(mock_urlopen, dummy_trajectory, dummy_drift_report):
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    alerter = WebhookAlerter(webhook_url="http://example.com/webhook")
    alerter.notify_drift(dummy_trajectory, dummy_drift_report)

    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    
    import json
    data = json.loads(req.data.decode("utf-8"))
    assert "text" in data
    assert "BEHAVIORAL DRIFT DETECTED" in data["text"]
    assert "RCR declining" in data["text"]


def test_composite_alerter(dummy_trajectory, dummy_drift_report):
    mock_alerter1 = MagicMock()
    mock_alerter2 = MagicMock()

    alerter = CompositeAlerter([mock_alerter1, mock_alerter2])

    alerter.notify_block(dummy_trajectory)
    mock_alerter1.notify_block.assert_called_once_with(dummy_trajectory)
    mock_alerter2.notify_block.assert_called_once_with(dummy_trajectory)

    alerter.notify_drift(dummy_trajectory, dummy_drift_report)
    mock_alerter1.notify_drift.assert_called_once_with(dummy_trajectory, dummy_drift_report)
    mock_alerter2.notify_drift.assert_called_once_with(dummy_trajectory, dummy_drift_report)

"""
Real-time Alerting Layer for Verifiable Observability.

Provides interfaces and implementations to dispatch real-time alerts
to external systems (like Slack, PagerDuty, or Webhooks) when
an agent's trajectory is BLOCKED or when behavioral DRIFT is detected.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from verifiable_observability.core.metrics import DriftReport
from verifiable_observability.storage.models import Trajectory

logger = logging.getLogger(__name__)


class AlerterBase(ABC):
    """Abstract interface for the alerting layer."""

    @abstractmethod
    def notify_block(self, trajectory: Trajectory) -> None:
        """
        Triggered when a trajectory is blocked by the CCM or Input Guardrails.

        Args:
            trajectory: The Trajectory object that was blocked.
        """
        pass

    @abstractmethod
    def notify_drift(self, trajectory: Trajectory, drift_report: DriftReport) -> None:
        """
        Triggered when behavioral drift is detected for a completed trajectory.

        Args:
            trajectory: The completed Trajectory object.
            drift_report: The generated DriftReport detailing the degradation.
        """
        pass


class WebhookAlerter(AlerterBase):
    """
    Sends JSON-formatted alerts to an HTTP webhook URL.
    Useful for generic integrations, Slack, Discord, or Microsoft Teams.
    """

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def _post(self, payload: dict[str, Any]) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status not in (200, 201, 202, 204):
                    logger.warning(
                        "Webhook alert failed with status %d", response.status
                    )
        except Exception as e:
            logger.error("Failed to send webhook alert: %s", e)

    def notify_block(self, trajectory: Trajectory) -> None:
        payload = {
            "text": (
                f"🚨 *AGENT BLOCKED* 🚨\n"
                f"*Trajectory ID:* {trajectory.trajectory_id}\n"
                f"*Domain:* {trajectory.task.domain.value}\n"
                f"*Task:* {trajectory.task.description}\n"
                f"*Reason:* {trajectory.failure_reason}"
            )
        }
        self._post(payload)

    def notify_drift(self, trajectory: Trajectory, drift_report: DriftReport) -> None:
        reasons_text = "\n".join(f"• {r}" for r in drift_report.drift_reasons)
        payload = {
            "text": (
                f"⚠️ *BEHAVIORAL DRIFT DETECTED* ⚠️\n"
                f"*Trajectory ID:* {trajectory.trajectory_id}\n"
                f"*Backend:* {trajectory.agent_backend} / {trajectory.model_name}\n"
                f"*Reasons:*\n{reasons_text}"
            )
        }
        self._post(payload)


class CompositeAlerter(AlerterBase):
    """
    Chains multiple alerters together.
    """

    def __init__(self, alerters: list[AlerterBase]) -> None:
        self.alerters = alerters

    def notify_block(self, trajectory: Trajectory) -> None:
        for alerter in self.alerters:
            alerter.notify_block(trajectory)

    def notify_drift(self, trajectory: Trajectory, drift_report: DriftReport) -> None:
        for alerter in self.alerters:
            alerter.notify_drift(trajectory, drift_report)

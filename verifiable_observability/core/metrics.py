"""
Metrics Engine — computes RCR and CCR per turn and aggregates per trajectory.

Phase 0: abstract interface only.
Phase 5: full implementation with trend analysis.

Metrics:
    RCR (Reasoning Consistency Ratio)
        = (decisions matched to a verified rule) / (total decisions in turn)

    CCR (Constraint Compliance Ratio)
        = (actions that received ALLOW from CCM) / (total actions in turn)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from verifiable_observability.storage.models import (
    ComplianceDecision,
    RuleCheckResult,
    Trajectory,
    Turn,
    TurnMetrics,
    VerificationStatus,
)


class MetricsEngineBase(ABC):
    """Abstract interface for the Metrics Engine."""

    @abstractmethod
    def record_turn(self, turn: Turn) -> TurnMetrics:
        """
        Compute and return metrics for a completed turn.

        Args:
            turn: The completed Turn (with rule_checks and constraint_checks populated).

        Returns:
            TurnMetrics with rcr and ccr for this turn.
        """
        ...

    @abstractmethod
    def compute_rcr(self, turn: Turn) -> float | None:
        """Compute RCR for a single turn. Returns None if no decisions."""
        ...

    @abstractmethod
    def compute_ccr(self, turn: Turn) -> float | None:
        """Compute CCR for a single turn. Returns None if no actions."""
        ...

    @abstractmethod
    def trajectory_summary(self, trajectory: Trajectory) -> dict:
        """Return per-turn RCR/CCR series and trajectory-level stats."""
        ...


class BasicMetricsEngine(MetricsEngineBase):
    """
    Concrete metrics engine used from Phase 0 onwards.

    Computes RCR and CCR from the rule_checks and constraint_checks
    already attached to each Turn by the Orchestrator.
    """

    def compute_rcr(self, turn: Turn) -> float | None:
        """
        RCR = matched-to-verified-rule decisions / total decisions.

        A rule check counts as "traceable" if matched=True AND the matched
        rule has verification_status=verified.  We track this via the
        match_method not being "none" and matched=True — the Rule Bank
        already filters to verified rules before returning a match.
        """
        if not turn.rule_checks:
            return None
        matched = sum(1 for rc in turn.rule_checks if rc.matched)
        return matched / len(turn.rule_checks)

    def compute_ccr(self, turn: Turn) -> float | None:
        """CCR = ALLOW actions / total actions proposed."""
        if not turn.constraint_checks:
            return None
        allowed = sum(
            1
            for cc in turn.constraint_checks
            if cc.decision == ComplianceDecision.ALLOW
        )
        return allowed / len(turn.constraint_checks)

    def record_turn(self, turn: Turn) -> TurnMetrics:
        metrics = TurnMetrics(
            rcr=self.compute_rcr(turn),
            ccr=self.compute_ccr(turn),
        )
        turn.metrics = metrics
        return metrics

    def trajectory_summary(self, trajectory: Trajectory) -> dict:
        rcr_series = [
            t.metrics.rcr for t in trajectory.turns if t.metrics.rcr is not None
        ]
        ccr_series = [
            t.metrics.ccr for t in trajectory.turns if t.metrics.ccr is not None
        ]

        def safe_avg(series: list[float]) -> float | None:
            return sum(series) / len(series) if series else None

        return {
            "trajectory_id": trajectory.trajectory_id,
            "outcome": trajectory.outcome.value,
            "num_turns": len(trajectory.turns),
            "rcr_per_turn": rcr_series,
            "ccr_per_turn": ccr_series,
            "avg_rcr": safe_avg(rcr_series),
            "avg_ccr": safe_avg(ccr_series),
        }

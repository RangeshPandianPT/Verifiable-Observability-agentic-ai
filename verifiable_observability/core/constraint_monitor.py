"""
Constraint Compliance Monitor (CCM) — enforces safety constraints before action dispatch.

Phase 0: abstract interface + stub (always ALLOW).
Phase 3: full implementation with Finance constraint set.

Decision outcomes:
    ALLOW  — action is safe to dispatch
    BLOCK  — hard constraint violated; action must NOT be dispatched
    FLAG   — soft constraint violated; action is escalated for review but may proceed
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    ConstraintCheckResult,
    Trajectory,
)


class ConstraintComplianceMonitorBase(ABC):
    """Abstract interface for the Constraint Compliance Monitor."""

    @abstractmethod
    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        """
        Evaluate an action against all active constraints.

        Args:
            action:     The Action the agent intends to dispatch.
            trajectory: Current trajectory (provides context, history).

        Returns:
            ConstraintCheckResult with ALLOW / BLOCK / FLAG decision.
        """
        ...


class StubCCM(ConstraintComplianceMonitorBase):
    """
    Pass-through stub — always returns ALLOW.

    Used in Phase 0 smoke tests. Replaced by the real CCM in Phase 3.
    """

    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=ComplianceDecision.ALLOW,
            violated_constraints=[],
            details="stub: no constraints checked",
        )

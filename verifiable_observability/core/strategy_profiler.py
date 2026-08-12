"""
Strategy Profiler — classifies an incoming Task to establish a behavioral baseline.

Phase 0: abstract interface + stub implementation.
Phase 2: full rule-based classifier (no LLM required).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from verifiable_observability.storage.models import (
    Domain,
    RiskTier,
    StrategyProfile,
    Task,
)


class StrategyProfilerBase(ABC):
    """
    Abstract base class for the Strategy Profiler.

    Implementations must classify a Task into a StrategyProfile that downstream
    layers (Rule Bank, CCM) use to scope their checks.
    """

    @abstractmethod
    def classify(self, task: Task) -> StrategyProfile:
        """
        Classify a task and return a StrategyProfile.

        Args:
            task: The incoming Task to classify.

        Returns:
            StrategyProfile with domain, task_type, risk_tier, etc.
        """
        ...


class StubStrategyProfiler(StrategyProfilerBase):
    """
    Pass-through stub used in Phase 0 smoke tests.

    Always returns a LOW-risk FINANCE profile. Replaced by the real
    classifier in Phase 2.
    """

    def classify(self, task: Task) -> StrategyProfile:
        return StrategyProfile(
            task_id=task.task_id,
            domain=task.domain,
            task_type="unknown",
            risk_tier=RiskTier.LOW,
            expected_turn_range=(1, 5),
            active_constraint_set_id="stub_constraints",
            active_rule_bank_scope=[],
        )

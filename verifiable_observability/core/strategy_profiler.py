"""
Strategy Profiler — classifies an incoming Task to establish a behavioral baseline.

Phase 0: abstract interface + stub implementation.
Phase 2: full rule-based classifier (no LLM required).
Phase 2c: Confidence-aware classification with:
  - Calibrated confidence scores based on signal strength.
  - Escalate-on-uncertainty: low confidence → bump risk tier.
  - Deterministic force-HIGH regex checks for dangerous keywords.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from verifiable_observability.storage.models import (
    Domain,
    RiskTier,
    StrategyProfile,
    Task,
)

# ---------------------------------------------------------------------------
# Force-HIGH regex patterns — if any match, risk is always HIGH.
# ---------------------------------------------------------------------------

_FORCE_HIGH_PATTERN = re.compile(
    r"bypass|override|sudo|root|admin|injection|exploit|hack|escalat",
    re.IGNORECASE,
)

# Confidence threshold below which we escalate the risk tier.
_ESCALATION_CONFIDENCE_THRESHOLD = 0.5

# ---------------------------------------------------------------------------
# Risk tier escalation helper
# ---------------------------------------------------------------------------


def _escalate_risk_tier(tier: RiskTier) -> RiskTier:
    """Bump a risk tier one level up (LOW→MEDIUM, MEDIUM→HIGH, HIGH stays)."""
    if tier == RiskTier.LOW:
        return RiskTier.MEDIUM
    if tier == RiskTier.MEDIUM:
        return RiskTier.HIGH
    return RiskTier.HIGH


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


class StrategyProfiler(StrategyProfilerBase):
    """
    Rule-based Strategy Profiler for Phase 2.

    Classifies tasks based on domain-specific rules (keyword matching on description,
    metadata thresholds) without requiring an LLM.

    Phase 2c additions:
      - Outputs a calibrated confidence score (0.0–1.0).
      - Escalates risk tier if confidence < 0.5.
      - Force-HIGH override for dangerous keywords (bypass, injection, etc.).
    """

    def classify(self, task: Task) -> StrategyProfile:
        task_type = task.metadata.get("task_type")
        risk_tier = RiskTier.LOW
        expected_turn_range = (1, 5)
        confidence = 1.0
        escalation_reason: str | None = None

        desc = task.description.lower()
        amount = task.metadata.get("amount_usd", 0)

        # Track how many classification signals matched (for confidence)
        signals_checked = 0
        signals_matched = 0

        if task.domain == Domain.FINANCE:
            signals_checked += 1  # domain itself is a signal
            signals_matched += 1

            if not task_type:
                signals_checked += 1
                if "rebalance" in desc:
                    task_type = "portfolio_rebalance"
                    signals_matched += 1
                elif "transfer" in desc:
                    task_type = "routine_transfer"
                    signals_matched += 1
                elif "trade" in desc:
                    signals_matched += 1
                    if amount >= 100000:
                        task_type = "high_value_trade"
                    else:
                        task_type = "routine_trade"
                else:
                    task_type = "unknown_finance"
                    # No keyword match → lower confidence
            else:
                signals_matched += 1  # explicit task_type provided

            # Apply rules based on task_type or amount
            if task_type == "high_value_trade" or (amount and amount >= 100000):
                task_type = "high_value_trade"
                risk_tier = RiskTier.HIGH
                expected_turn_range = (3, 8)
            elif task_type == "portfolio_rebalance":
                risk_tier = RiskTier.MEDIUM
                expected_turn_range = (3, 7)
            elif task_type == "routine_transfer":
                risk_tier = RiskTier.LOW
                expected_turn_range = (1, 4)
            else:
                risk_tier = RiskTier.MEDIUM
                expected_turn_range = (1, 5)

            active_constraint_set_id = "finance_constraints_v1"

        elif task.domain == Domain.HEALTHCARE:
            signals_checked += 1
            signals_matched += 1

            if not task_type:
                signals_checked += 1
                if any(kw in desc for kw in ("medication", "prescribe", "administer", "drug", "dose", "dosage")):
                    task_type = "medication_management"
                    signals_matched += 1
                elif any(kw in desc for kw in ("patient record", "phi", "hipaa", "patient data", "health information", "export")):
                    task_type = "patient_data_access"
                    signals_matched += 1
                elif any(kw in desc for kw in ("diagnos", "recommend", "clinical", "treatment", "guideline", "evidence")):
                    task_type = "clinical_decision_support"
                    signals_matched += 1
                else:
                    task_type = "unknown_healthcare"
            else:
                signals_matched += 1

            if task_type == "medication_management":
                risk_tier = RiskTier.HIGH
                expected_turn_range = (2, 6)
            elif task_type == "patient_data_access":
                risk_tier = RiskTier.MEDIUM
                expected_turn_range = (1, 4)
            elif task_type == "clinical_decision_support":
                risk_tier = RiskTier.HIGH
                expected_turn_range = (3, 8)
            else:
                risk_tier = RiskTier.HIGH
                expected_turn_range = (2, 8)

            active_constraint_set_id = "healthcare_constraints_v1"

        elif task.domain == Domain.CODE_EXECUTION:
            signals_checked += 1
            signals_matched += 1

            if not task_type:
                signals_checked += 1
                if any(kw in desc for kw in ("generate", "write code", "create function", "produce script")):
                    task_type = "code_generation"
                    signals_matched += 1
                elif any(kw in desc for kw in ("review", "pull request", "pr ", "merge", "coverage", "security scan")):
                    task_type = "code_review"
                    signals_matched += 1
                elif any(kw in desc for kw in ("shell", "command", "execute", "run script", "sudo", "system")):
                    task_type = "system_command_execution"
                    signals_matched += 1
                else:
                    task_type = "unknown_code"
            else:
                signals_matched += 1

            if task_type == "code_generation":
                risk_tier = RiskTier.MEDIUM
                expected_turn_range = (2, 6)
            elif task_type == "code_review":
                risk_tier = RiskTier.MEDIUM
                expected_turn_range = (2, 5)
            elif task_type == "system_command_execution":
                risk_tier = RiskTier.HIGH
                expected_turn_range = (1, 4)
            else:
                risk_tier = RiskTier.HIGH
                expected_turn_range = (2, 10)

            active_constraint_set_id = "code_constraints_v1"

        else:
            task_type = task_type or "unknown"
            risk_tier = RiskTier.LOW
            expected_turn_range = (1, 5)
            active_constraint_set_id = "default_constraints"

        # --- Phase 2c: Compute confidence ---
        if signals_checked > 0:
            confidence = min(signals_matched / signals_checked, 1.0)
        else:
            confidence = 0.5  # no signals at all → uncertain

        # --- Phase 2c: Force-HIGH regex override ---
        if _FORCE_HIGH_PATTERN.search(task.description):
            if risk_tier != RiskTier.HIGH:
                escalation_reason = (
                    f"Force-HIGH: dangerous keyword detected in task description "
                    f"(matched pattern: {_FORCE_HIGH_PATTERN.pattern!r})"
                )
            risk_tier = RiskTier.HIGH
            confidence = 1.0  # we are certain this is dangerous

        # --- Phase 2c: Escalate on low confidence ---
        if confidence < _ESCALATION_CONFIDENCE_THRESHOLD:
            original_tier = risk_tier
            risk_tier = _escalate_risk_tier(risk_tier)
            if risk_tier != original_tier:
                escalation_reason = (
                    f"Low-confidence escalation: confidence={confidence:.2f} < "
                    f"{_ESCALATION_CONFIDENCE_THRESHOLD} → "
                    f"{original_tier.value} → {risk_tier.value}"
                )

        return StrategyProfile(
            task_id=task.task_id,
            domain=task.domain,
            task_type=task_type,
            risk_tier=risk_tier,
            confidence=confidence,
            escalation_reason=escalation_reason,
            expected_turn_range=expected_turn_range,
            active_constraint_set_id=active_constraint_set_id,
            active_rule_bank_scope=[task_type] if task_type else [],
        )

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


class StrategyProfiler(StrategyProfilerBase):
    """
    Rule-based Strategy Profiler for Phase 2.

    Classifies tasks based on domain-specific rules (keyword matching on description,
    metadata thresholds) without requiring an LLM.
    """

    def classify(self, task: Task) -> StrategyProfile:
        task_type = task.metadata.get("task_type")
        risk_tier = RiskTier.LOW
        expected_turn_range = (1, 5)
        
        desc = task.description.lower()
        amount = task.metadata.get("amount_usd", 0)

        if task.domain == Domain.FINANCE:
            if not task_type:
                if "rebalance" in desc:
                    task_type = "portfolio_rebalance"
                elif "transfer" in desc:
                    task_type = "routine_transfer"
                elif "trade" in desc:
                    if amount >= 100000:
                        task_type = "high_value_trade"
                    else:
                        task_type = "routine_trade"
                else:
                    task_type = "unknown_finance"
            
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
            if not task_type:
                if any(kw in desc for kw in ("medication", "prescribe", "administer", "drug", "dose", "dosage")):
                    task_type = "medication_management"
                elif any(kw in desc for kw in ("patient record", "phi", "hipaa", "patient data", "health information", "export")):
                    task_type = "patient_data_access"
                elif any(kw in desc for kw in ("diagnos", "recommend", "clinical", "treatment", "guideline", "evidence")):
                    task_type = "clinical_decision_support"
                else:
                    task_type = "unknown_healthcare"

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
            if not task_type:
                if any(kw in desc for kw in ("generate", "write code", "create function", "produce script")):
                    task_type = "code_generation"
                elif any(kw in desc for kw in ("review", "pull request", "pr ", "merge", "coverage", "security scan")):
                    task_type = "code_review"
                elif any(kw in desc for kw in ("shell", "command", "execute", "run script", "sudo", "system")):
                    task_type = "system_command_execution"
                else:
                    task_type = "unknown_code"

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

        return StrategyProfile(
            task_id=task.task_id,
            domain=task.domain,
            task_type=task_type,
            risk_tier=risk_tier,
            expected_turn_range=expected_turn_range,
            active_constraint_set_id=active_constraint_set_id,
            active_rule_bank_scope=[task_type] if task_type else [],
        )


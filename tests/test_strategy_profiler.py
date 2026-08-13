from __future__ import annotations

import pytest

from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.storage.models import Domain, RiskTier, Task


class TestStrategyProfiler:
    @pytest.fixture
    def profiler(self):
        return StrategyProfiler()

    def test_finance_routine_transfer(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Execute a routine transfer of $500.",
            metadata={"amount_usd": 500}
        )
        profile = profiler.classify(task)
        
        assert profile.domain == Domain.FINANCE
        assert profile.task_type == "routine_transfer"
        assert profile.risk_tier == RiskTier.LOW
        assert profile.active_constraint_set_id == "finance_constraints_v1"
        assert "routine_transfer" in profile.active_rule_bank_scope

    def test_finance_high_value_trade(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Submit a trade order for AAPL shares.",
            metadata={"amount_usd": 150000}
        )
        profile = profiler.classify(task)
        
        assert profile.domain == Domain.FINANCE
        assert profile.task_type == "high_value_trade"
        assert profile.risk_tier == RiskTier.HIGH
        assert profile.active_constraint_set_id == "finance_constraints_v1"

    def test_finance_portfolio_rebalance(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Rebalance the portfolio to 60/40.",
            metadata={}
        )
        profile = profiler.classify(task)
        
        assert profile.domain == Domain.FINANCE
        assert profile.task_type == "portfolio_rebalance"
        assert profile.risk_tier == RiskTier.MEDIUM
        assert profile.active_constraint_set_id == "finance_constraints_v1"
        
    def test_finance_explicit_task_type(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Some description that doesn't mention keywords.",
            metadata={"task_type": "high_value_trade"}
        )
        profile = profiler.classify(task)
        assert profile.task_type == "high_value_trade"
        assert profile.risk_tier == RiskTier.HIGH

    def test_healthcare_domain(self, profiler):
        task = Task(
            domain=Domain.HEALTHCARE,
            description="Analyze patient records."
        )
        profile = profiler.classify(task)
        assert profile.domain == Domain.HEALTHCARE
        assert profile.risk_tier == RiskTier.HIGH
        assert profile.active_constraint_set_id == "healthcare_constraints_v1"

    def test_code_execution_domain(self, profiler):
        task = Task(
            domain=Domain.CODE_EXECUTION,
            description="Run this python script."
        )
        profile = profiler.classify(task)
        assert profile.domain == Domain.CODE_EXECUTION
        assert profile.risk_tier == RiskTier.HIGH
        assert profile.active_constraint_set_id == "code_constraints_v1"

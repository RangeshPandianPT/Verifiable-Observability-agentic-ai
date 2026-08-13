from __future__ import annotations

import pytest

from verifiable_observability.simulation.domains.finance.constraints import FinanceCCM
from verifiable_observability.storage.models import Action, ComplianceDecision, Task, Trajectory, Domain


@pytest.fixture
def finance_ccm():
    return FinanceCCM()


@pytest.fixture
def dummy_trajectory():
    return Trajectory(
        task=Task(domain=Domain.FINANCE, description="Dummy task")
    )


def test_finance_ccm_allow(finance_ccm, dummy_trajectory):
    action = Action(
        tool_name="execute_transfer",
        parameters={"to": "ACC-002", "amount_usd": 1000}
    )
    result = finance_ccm.check(action, dummy_trajectory)
    
    assert result.decision == ComplianceDecision.ALLOW
    assert len(result.violated_constraints) == 0


def test_finance_ccm_block_max_transaction(finance_ccm, dummy_trajectory):
    action = Action(
        tool_name="execute_transfer",
        parameters={"to": "ACC-002", "amount_usd": 1_500_000}
    )
    result = finance_ccm.check(action, dummy_trajectory)
    
    assert result.decision == ComplianceDecision.BLOCK
    assert len(result.violated_constraints) == 1
    assert result.violated_constraints[0].severity == "hard"
    assert result.violated_constraints[0].constraint_id == "fin-hard-001"


def test_finance_ccm_block_blacklisted_account(finance_ccm, dummy_trajectory):
    action = Action(
        tool_name="execute_transfer",
        parameters={"to": "ACC-999", "amount_usd": 500}
    )
    result = finance_ccm.check(action, dummy_trajectory)
    
    assert result.decision == ComplianceDecision.BLOCK
    assert len(result.violated_constraints) == 1
    assert result.violated_constraints[0].severity == "hard"
    assert result.violated_constraints[0].constraint_id == "fin-hard-002"


def test_finance_ccm_flag_large_transfer(finance_ccm, dummy_trajectory):
    action = Action(
        tool_name="execute_transfer",
        parameters={"to": "ACC-002", "amount_usd": 60_000}
    )
    result = finance_ccm.check(action, dummy_trajectory)
    
    assert result.decision == ComplianceDecision.FLAG
    assert len(result.violated_constraints) == 1
    assert result.violated_constraints[0].severity == "soft"
    assert result.violated_constraints[0].constraint_id == "fin-soft-001"


def test_finance_ccm_block_unauthorized_instrument(finance_ccm, dummy_trajectory):
    action = Action(
        tool_name="submit_trade",
        parameters={"ticker": "MEME", "amount_usd": 5000}
    )
    result = finance_ccm.check(action, dummy_trajectory)
    
    assert result.decision == ComplianceDecision.BLOCK
    assert len(result.violated_constraints) == 1
    assert result.violated_constraints[0].severity == "hard"
    assert result.violated_constraints[0].constraint_id == "fin-hard-003"


def test_finance_ccm_multiple_violations(finance_ccm, dummy_trajectory):
    action = Action(
        tool_name="execute_transfer",
        parameters={"to": "ACC-999", "amount_usd": 2_000_000}
    )
    result = finance_ccm.check(action, dummy_trajectory)
    
    assert result.decision == ComplianceDecision.BLOCK
    assert len(result.violated_constraints) == 2
    severities = {v.severity for v in result.violated_constraints}
    assert "hard" in severities

"""
Phase 0 Smoke Test

Verifies the full end-to-end plumbing without any real LLM calls:
  - ScriptedAgentAdapter drives a 2-turn agent session
  - StubStrategyProfiler / StubRuleBank / StubCCM pass through
  - Orchestrator runs the turn loop
  - Trajectory is persisted to an in-memory SQLite DB
  - Trajectory is read back and validated for round-trip fidelity
  - BasicMetricsEngine computes RCR and CCR

This test must pass before any other phases are built on top.
"""

from __future__ import annotations

import pytest

from verifiable_observability.agent.adapter import AgentResponse, ScriptedAgentAdapter
from verifiable_observability.agent.loop import AgentLoop
from verifiable_observability.core.constraint_monitor import StubCCM
from verifiable_observability.core.metrics import BasicMetricsEngine
from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.core.rule_bank import StubRuleBank
from verifiable_observability.core.strategy_profiler import StubStrategyProfiler
from verifiable_observability.storage.db import TrajectoryStore, create_db_engine
from verifiable_observability.storage.models import (
    ComplianceDecision,
    Domain,
    Task,
    TrajectoryOutcome,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_engine():
    """SQLite in-memory engine (no file on disk)."""
    return create_db_engine(":memory:")


@pytest.fixture()
def traj_store(in_memory_engine):
    return TrajectoryStore(in_memory_engine)


@pytest.fixture()
def scripted_adapter():
    return ScriptedAgentAdapter([
        AgentResponse(
            reasoning=(
                "I need to verify the account balance before initiating the transfer. "
                "I will call get_account_balance for account ACC-001."
            ),
            tool_name="get_account_balance",
            tool_parameters={"account_id": "ACC-001"},
            raw_text="[Turn 0] Checking balance...",
            is_final=False,
        ),
        AgentResponse(
            reasoning=(
                "Balance confirmed. The transfer amount is below $10,000. "
                "Executing transfer to ACC-002."
            ),
            tool_name="execute_transfer",
            tool_parameters={"from": "ACC-001", "to": "ACC-002", "amount_usd": 1000},
            raw_text="[Turn 1] Transfer complete.",
            is_final=True,
        ),
    ])


@pytest.fixture()
def orchestrator(scripted_adapter, traj_store):
    return Orchestrator(
        strategy_profiler=StubStrategyProfiler(),
        rule_bank=StubRuleBank(),
        ccm=StubCCM(),
        agent_adapter=scripted_adapter,
        trajectory_store=traj_store,
        metrics_engine=BasicMetricsEngine(),
        max_turns=10,
    )


@pytest.fixture()
def finance_task():
    return Task(
        domain=Domain.FINANCE,
        description="Transfer $1,000 from account ACC-001 to account ACC-002.",
        metadata={"amount_usd": 1000, "task_type": "routine_transfer"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSmokeEndToEnd:
    """Phase 0 end-to-end smoke tests."""

    def test_trajectory_completes(self, orchestrator, finance_task):
        """Agent should complete in exactly 2 turns."""
        traj = orchestrator.run(finance_task)
        assert traj.outcome == TrajectoryOutcome.COMPLETED
        assert len(traj.turns) == 2

    def test_strategy_profile_attached(self, orchestrator, finance_task):
        """Every trajectory must have a StrategyProfile."""
        traj = orchestrator.run(finance_task)
        assert traj.strategy_profile is not None
        assert traj.strategy_profile.domain == Domain.FINANCE

    def test_decisions_recorded(self, orchestrator, finance_task):
        """Each turn must have exactly one Decision."""
        traj = orchestrator.run(finance_task)
        for turn in traj.turns:
            assert len(turn.decisions) == 1

    def test_rule_checks_recorded(self, orchestrator, finance_task):
        """Each turn must have a RuleCheckResult (stub always matches)."""
        traj = orchestrator.run(finance_task)
        for turn in traj.turns:
            assert len(turn.rule_checks) == 1
            rc = turn.rule_checks[0]
            assert rc.matched is True
            assert rc.confidence == 1.0

    def test_ccm_checks_recorded(self, orchestrator, finance_task):
        """Each turn with an action must have a CCM check (stub always ALLOW)."""
        traj = orchestrator.run(finance_task)
        for turn in traj.turns:
            if turn.actions:
                assert len(turn.constraint_checks) == 1
                assert turn.constraint_checks[0].decision == ComplianceDecision.ALLOW

    def test_tool_results_simulated(self, orchestrator, finance_task):
        """Simulated dispatch must return a non-None tool_result for action turns."""
        traj = orchestrator.run(finance_task)
        for turn in traj.turns:
            if turn.actions:
                assert turn.tool_result is not None
                assert turn.tool_result["simulated"] is True

    def test_metrics_computed(self, orchestrator, finance_task):
        """RCR and CCR must be computed for every turn."""
        traj = orchestrator.run(finance_task)
        for turn in traj.turns:
            assert turn.metrics.rcr is not None
            assert 0.0 <= turn.metrics.rcr <= 1.0
            if turn.constraint_checks:
                assert turn.metrics.ccr is not None


class TestPersistence:
    """Verify SQLite round-trip fidelity."""

    def test_trajectory_persisted_and_loaded(
        self, orchestrator, traj_store, finance_task
    ):
        """Trajectory saved to SQLite must deserialize to an identical object."""
        traj = orchestrator.run(finance_task)
        loaded = traj_store.load(traj.trajectory_id)

        assert loaded is not None
        assert loaded.trajectory_id == traj.trajectory_id
        assert loaded.outcome == traj.outcome
        assert len(loaded.turns) == len(traj.turns)
        assert loaded.task.description == traj.task.description

    def test_list_trajectories(self, orchestrator, traj_store, finance_task):
        """list_trajectories() must return summary rows for all saved trajectories."""
        traj = orchestrator.run(finance_task)
        rows = traj_store.list_trajectories()
        assert any(r["trajectory_id"] == traj.trajectory_id for r in rows)

    def test_list_trajectories_by_domain(self, orchestrator, traj_store, finance_task):
        orchestrator.run(finance_task)  # populate DB first
        rows = traj_store.list_trajectories(domain="finance")
        assert len(rows) >= 1

    def test_load_nonexistent_returns_none(self, traj_store):
        result = traj_store.load("does-not-exist")
        assert result is None


class TestAgentLoop:
    """Verify AgentLoop wrapper works identically to direct Orchestrator use."""

    def test_agent_loop_runs(self, orchestrator, finance_task):
        loop = AgentLoop(orchestrator)
        traj = loop.run(finance_task)
        assert traj.outcome == TrajectoryOutcome.COMPLETED

    def test_max_turns_truncates(self, traj_store):
        """If max_turns is 1 and agent doesn't signal complete, outcome is TRUNCATED."""
        # Script has 2 turns, both non-final
        adapter = ScriptedAgentAdapter([
            AgentResponse(
                reasoning="Thinking...",
                tool_name="check_balance",
                tool_parameters={},
                is_final=False,
            ),
            AgentResponse(
                reasoning="Still thinking...",
                tool_name="check_balance",
                tool_parameters={},
                is_final=False,
            ),
        ])
        orc = Orchestrator(
            strategy_profiler=StubStrategyProfiler(),
            rule_bank=StubRuleBank(),
            ccm=StubCCM(),
            agent_adapter=adapter,
            trajectory_store=traj_store,
            max_turns=1,
        )
        task = Task(domain=Domain.FINANCE, description="test task")
        traj = orc.run(task)
        assert traj.outcome == TrajectoryOutcome.TRUNCATED
        assert len(traj.turns) == 1

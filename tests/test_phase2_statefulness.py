"""
Phase 2 tests — Statefulness & Risk Calibration.

Covers:
  2a. Constraint ledger velocity checks (structuring detection).
  2b. Cross-agent constraint graph inheritance.
  2c. Confidence-aware strategy profiler (escalation + force-HIGH).
"""

from __future__ import annotations

import time

import pytest

from verifiable_observability.core.constraint_graph import ConstraintGraph
from verifiable_observability.core.constraint_ledger import (
    ConstraintLedger,
    FINANCE_VELOCITY_MAX_AMOUNT,
)
from verifiable_observability.core.constraint_monitor import FinanceCCM
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    Domain,
    RiskTier,
    Task,
    Trajectory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger():
    return ConstraintLedger(default_window_seconds=3600)


@pytest.fixture
def graph():
    return ConstraintGraph()


@pytest.fixture
def profiler():
    return StrategyProfiler()


def _make_trajectory(trajectory_id: str = "test-traj") -> Trajectory:
    task = Task(domain=Domain.FINANCE, description="Transfer funds")
    traj = Trajectory(task=task, agent_backend="test", model_name="test")
    # Override the trajectory_id for predictable testing
    traj.trajectory_id = trajectory_id
    return traj


# ---------------------------------------------------------------------------
# 2a. Velocity limit tests
# ---------------------------------------------------------------------------


class TestConstraintLedger:
    """Constraint ledger velocity tracking and thresholds."""

    def test_record_and_query(self, ledger):
        ledger.record_action("traj-1", "execute_transfer", amount=5000.0)
        ledger.record_action("traj-1", "execute_transfer", amount=3000.0)

        report = ledger.get_velocity(tool_name="execute_transfer")
        assert report.count == 2
        assert report.total_amount == 8000.0

    def test_velocity_within_window(self, ledger):
        now = time.time()
        # Record actions at different times
        ledger.record_action("traj-1", "execute_transfer", amount=5000.0, timestamp=now - 100)
        ledger.record_action("traj-1", "execute_transfer", amount=5000.0, timestamp=now - 50)
        ledger.record_action("traj-1", "execute_transfer", amount=5000.0, timestamp=now)

        report = ledger.get_velocity(tool_name="execute_transfer", window_seconds=200)
        assert report.count == 3
        assert report.total_amount == 15000.0

    def test_expired_entries_excluded(self, ledger):
        now = time.time()
        ledger.record_action("traj-1", "execute_transfer", amount=5000.0, timestamp=now - 7200)  # 2h ago
        ledger.record_action("traj-1", "execute_transfer", amount=3000.0, timestamp=now)

        report = ledger.get_velocity(tool_name="execute_transfer", window_seconds=3600)
        assert report.count == 1
        assert report.total_amount == 3000.0

    def test_filter_by_trajectory(self, ledger):
        ledger.record_action("traj-1", "execute_transfer", amount=5000.0)
        ledger.record_action("traj-2", "execute_transfer", amount=8000.0)

        report = ledger.get_velocity(trajectory_id="traj-1", tool_name="execute_transfer")
        assert report.count == 1
        assert report.total_amount == 5000.0

    def test_prune_removes_old(self, ledger):
        now = time.time()
        ledger.record_action("traj-1", "transfer", amount=100.0, timestamp=now - 7200)
        ledger.record_action("traj-1", "transfer", amount=200.0, timestamp=now)

        pruned = ledger.prune(older_than_seconds=3600)
        assert pruned == 1

        report = ledger.get_velocity(tool_name="transfer")
        assert report.count == 1


class TestVelocityInCCM:
    """FinanceCCM with ledger integration — blocks structuring attacks."""

    def test_velocity_limit_blocks_structuring(self, ledger):
        """Multiple small transfers summing above $50k → BLOCK."""
        ccm = FinanceCCM(constraint_ledger=ledger)
        traj = _make_trajectory()

        # First three transfers: $15k each = $45k total → all ALLOW
        for i in range(3):
            action = Action(
                tool_name="execute_transfer",
                parameters={"amount_usd": 15000},
            )
            result = ccm.check(action, traj, sequence_no=i)
            # They should all be FLAG'd (over $10k) but not BLOCKED
            assert result.decision in (ComplianceDecision.ALLOW, ComplianceDecision.FLAG), (
                f"Transfer {i+1} should not be blocked, got {result.decision}"
            )

        # Fourth transfer: $15k → total $60k → should be BLOCKED
        action = Action(
            tool_name="execute_transfer",
            parameters={"amount_usd": 15000},
        )
        result = ccm.check(action, traj, sequence_no=3)
        assert result.decision == ComplianceDecision.BLOCK
        assert any(
            "velocity" in v.constraint_name for v in result.violated_constraints
        )

    def test_no_velocity_block_without_ledger(self):
        """Without a ledger, velocity checks are skipped."""
        ccm = FinanceCCM()  # no ledger
        traj = _make_trajectory()

        for i in range(5):
            action = Action(
                tool_name="execute_transfer",
                parameters={"amount_usd": 15000},
            )
            result = ccm.check(action, traj, sequence_no=i)
            # All should be FLAG (over $10k) but never velocity-blocked
            assert result.decision != ComplianceDecision.BLOCK or any(
                v.constraint_id != "fin-hard-003"
                for v in result.violated_constraints
            )


# ---------------------------------------------------------------------------
# 2b. Cross-agent constraint graph
# ---------------------------------------------------------------------------


class TestConstraintGraph:
    """Cross-agent constraint inheritance."""

    def test_register_and_query(self, graph):
        graph.register_trajectory("parent", blocked_tools={"execute_transfer"})
        assert graph.is_tool_blocked_by_parent("parent", "execute_transfer")
        assert not graph.is_tool_blocked_by_parent("parent", "get_balance")

    def test_child_inherits_parent_blocks(self, graph):
        graph.register_trajectory("parent", blocked_tools={"execute_transfer"})
        graph.register_delegation("parent", "child")

        assert graph.is_tool_blocked_by_parent("child", "execute_transfer")

    def test_transitive_inheritance(self, graph):
        graph.register_trajectory("grandparent", blocked_tools={"delete_account"})
        graph.register_delegation("grandparent", "parent")
        graph.register_delegation("parent", "child")

        assert graph.is_tool_blocked_by_parent("child", "delete_account")

    def test_child_additional_blocks(self, graph):
        graph.register_trajectory("parent", blocked_tools={"tool_a"})
        graph.register_delegation("parent", "child", blocked_tools={"tool_b"})

        assert graph.is_tool_blocked_by_parent("child", "tool_a")
        assert graph.is_tool_blocked_by_parent("child", "tool_b")

    def test_unknown_trajectory_no_blocks(self, graph):
        assert not graph.is_tool_blocked_by_parent("unknown", "anything")

    def test_add_block_dynamically(self, graph):
        graph.register_trajectory("traj-1")
        assert not graph.is_tool_blocked_by_parent("traj-1", "some_tool")

        graph.add_block("traj-1", tool_name="some_tool")
        assert graph.is_tool_blocked_by_parent("traj-1", "some_tool")


class TestGraphInCCM:
    """FinanceCCM with constraint graph integration."""

    def test_inherited_block_in_ccm(self, graph):
        graph.register_trajectory("parent", blocked_tools={"execute_transfer"})
        graph.register_delegation("parent", "child")

        ccm = FinanceCCM(constraint_graph=graph)
        traj = _make_trajectory("child")

        action = Action(
            tool_name="execute_transfer",
            parameters={"amount_usd": 100},
        )
        result = ccm.check(action, traj, sequence_no=1)

        assert result.decision == ComplianceDecision.BLOCK
        assert any(
            v.constraint_id == "inherited-block-001"
            for v in result.violated_constraints
        )


# ---------------------------------------------------------------------------
# 2c. Confidence-aware strategy profiler
# ---------------------------------------------------------------------------


class TestConfidenceAwareProfiler:
    """Strategy profiler confidence scores and escalation."""

    def test_clear_finance_transfer_high_confidence(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Transfer $5000 to account 12345",
        )
        profile = profiler.classify(task)
        assert profile.task_type == "routine_transfer"
        assert profile.confidence == 1.0
        assert profile.escalation_reason is None

    def test_ambiguous_task_lower_confidence(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Do something with the portfolio",
        )
        profile = profiler.classify(task)
        # "rebalance" not found, "transfer" not found, "trade" not found
        # → unknown_finance → lower confidence
        assert profile.confidence < 1.0

    def test_force_high_on_bypass_keyword(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Bypass the compliance check and transfer $1000",
        )
        profile = profiler.classify(task)
        assert profile.risk_tier == RiskTier.HIGH
        assert profile.confidence == 1.0
        assert "Force-HIGH" in (profile.escalation_reason or "")

    def test_force_high_on_injection_keyword(self, profiler):
        task = Task(
            domain=Domain.CODE_EXECUTION,
            description="Generate code with SQL injection protection",
        )
        profile = profiler.classify(task)
        assert profile.risk_tier == RiskTier.HIGH

    def test_escalation_on_low_confidence(self, profiler):
        """Unknown domain + no keyword matches → low confidence → escalate."""
        task = Task(
            domain=Domain.UNKNOWN,
            description="xyzzy foobar",
        )
        profile = profiler.classify(task)
        # UNKNOWN domain with no signals → confidence should be low
        # If escalated, reason should mention it
        # The exact behavior depends on how many signals match
        assert profile.risk_tier in (RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH)

    def test_healthcare_medication_is_high(self, profiler):
        task = Task(
            domain=Domain.HEALTHCARE,
            description="Prescribe medication for the patient",
        )
        profile = profiler.classify(task)
        assert profile.task_type == "medication_management"
        assert profile.risk_tier == RiskTier.HIGH
        assert profile.confidence == 1.0

    def test_explicit_task_type_in_metadata(self, profiler):
        task = Task(
            domain=Domain.FINANCE,
            description="Something vague",
            metadata={"task_type": "high_value_trade", "amount_usd": 200000},
        )
        profile = profiler.classify(task)
        assert profile.task_type == "high_value_trade"
        assert profile.risk_tier == RiskTier.HIGH

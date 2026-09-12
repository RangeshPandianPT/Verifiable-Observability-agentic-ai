"""
Phase 1 tests — Execution Integrity.

Covers:
  1a. Signed execution ticket round-trip, expiry, and tamper detection.
  1b. Risk-tier fail-safe (fail-closed for HIGH, fail-open for LOW).
  1c. Chaos testing: CCM crash/timeout → fail-safe behaviour.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from verifiable_observability.core.constraint_monitor import (
    ConstraintComplianceMonitorBase,
    FinanceCCM,
    StubCCM,
)
from verifiable_observability.core.execution_ticket import (
    TicketIssuer,
    TicketValidationError,
    TicketValidator,
    canonical_action_hash,
)
from verifiable_observability.core.fail_safe import (
    FailSafePolicy,
    apply_fail_safe,
    get_fail_safe_policy,
)
from verifiable_observability.core.metrics import BasicMetricsEngine
from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.core.rule_bank import StubRuleBank
from verifiable_observability.core.strategy_profiler import StubStrategyProfiler
from verifiable_observability.storage.db import TrajectoryStore, create_db_engine
from verifiable_observability.storage.models import (
    Action,
    AgentResponse,
    ComplianceDecision,
    ConstraintCheckResult,
    Domain,
    RiskTier,
    Task,
    Trajectory,
    TrajectoryOutcome,
    ViolatedConstraint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ticket_issuer():
    return TicketIssuer(secret=b"test-secret-key-32-bytes-padding!", ttl_seconds=5)


@pytest.fixture
def ticket_validator(ticket_issuer):
    return TicketValidator(secret=ticket_issuer.secret)


@pytest.fixture
def sample_action():
    return Action(tool_name="execute_transfer", parameters={"amount_usd": 5000})


@pytest.fixture
def sample_trajectory():
    task = Task(domain=Domain.FINANCE, description="Transfer $5000")
    return Trajectory(task=task, agent_backend="test", model_name="test")


@pytest.fixture
def db_engine():
    return create_db_engine(":memory:")


# ---------------------------------------------------------------------------
# 1a. Ticket signing / validation
# ---------------------------------------------------------------------------


class TestExecutionTicketRoundTrip:
    """Ticket issuance → validation → pass."""

    def test_valid_ticket_passes(self, ticket_issuer, ticket_validator, sample_action):
        ticket = ticket_issuer.issue(sample_action, "traj-1", sequence_no=1)

        # Should not raise
        ticket_validator.validate(ticket, sample_action, "traj-1", 1)

    def test_ticket_fields(self, ticket_issuer, sample_action):
        ticket = ticket_issuer.issue(sample_action, "traj-1", sequence_no=1)

        assert ticket.trajectory_id == "traj-1"
        assert ticket.sequence_no == 1
        assert ticket.action_hash  # non-empty
        assert ticket.signature  # non-empty
        assert ticket.expires_at > ticket.issued_at


class TestTicketExpiry:
    """Expired tickets are rejected."""

    def test_expired_ticket_rejected(self, ticket_validator, sample_action):
        # Create an issuer with 0-second TTL
        issuer = TicketIssuer(secret=ticket_validator._secret, ttl_seconds=0)
        ticket = issuer.issue(sample_action, "traj-1", sequence_no=1)

        # The ticket is already expired (TTL=0)
        # Give a tiny margin by sleeping
        time.sleep(0.01)

        with pytest.raises(TicketValidationError, match="expired"):
            ticket_validator.validate(ticket, sample_action, "traj-1", 1)


class TestTicketTampering:
    """Modified actions after ticket issuance are rejected."""

    def test_tampered_action_rejected(
        self, ticket_issuer, ticket_validator, sample_action
    ):
        ticket = ticket_issuer.issue(sample_action, "traj-1", sequence_no=1)

        # Tamper with the action
        tampered = Action(
            action_id=sample_action.action_id,
            tool_name="execute_transfer",
            parameters={"amount_usd": 999999},  # changed amount!
        )

        with pytest.raises(TicketValidationError, match="hash mismatch"):
            ticket_validator.validate(ticket, tampered, "traj-1", 1)

    def test_wrong_sequence_rejected(
        self, ticket_issuer, ticket_validator, sample_action
    ):
        ticket = ticket_issuer.issue(sample_action, "traj-1", sequence_no=1)

        with pytest.raises(TicketValidationError, match="hash mismatch"):
            ticket_validator.validate(ticket, sample_action, "traj-1", 2)

    def test_wrong_trajectory_rejected(
        self, ticket_issuer, ticket_validator, sample_action
    ):
        ticket = ticket_issuer.issue(sample_action, "traj-1", sequence_no=1)

        with pytest.raises(TicketValidationError, match="hash mismatch"):
            ticket_validator.validate(ticket, sample_action, "traj-WRONG", 1)

    def test_forged_signature_rejected(
        self, ticket_issuer, sample_action
    ):
        ticket = ticket_issuer.issue(sample_action, "traj-1", sequence_no=1)
        ticket.signature = "0" * 64  # forged

        validator = TicketValidator(secret=ticket_issuer.secret)
        with pytest.raises(TicketValidationError, match="signature"):
            validator.validate(ticket, sample_action, "traj-1", 1)


class TestCanonicalHash:
    """Deterministic hashing produces consistent results."""

    def test_deterministic(self):
        h1 = canonical_action_hash("tool", {"a": 1, "b": 2}, "traj", 0)
        h2 = canonical_action_hash("tool", {"b": 2, "a": 1}, "traj", 0)
        assert h1 == h2  # sorted keys → same hash

    def test_different_params_different_hash(self):
        h1 = canonical_action_hash("tool", {"a": 1}, "traj", 0)
        h2 = canonical_action_hash("tool", {"a": 2}, "traj", 0)
        assert h1 != h2


# ---------------------------------------------------------------------------
# 1b. Fail-safe policy
# ---------------------------------------------------------------------------


class TestFailSafePolicy:
    """Risk-tier-based fail-safe logic."""

    def test_high_risk_fails_closed(self):
        policy = get_fail_safe_policy(RiskTier.HIGH)
        assert policy == FailSafePolicy.FAIL_CLOSED

    def test_medium_risk_fails_closed(self):
        policy = get_fail_safe_policy(RiskTier.MEDIUM)
        assert policy == FailSafePolicy.FAIL_CLOSED

    def test_low_risk_fails_open(self):
        policy = get_fail_safe_policy(RiskTier.LOW)
        assert policy == FailSafePolicy.FAIL_OPEN

    def test_fail_closed_returns_block(self, sample_action):
        result = apply_fail_safe(
            FailSafePolicy.FAIL_CLOSED,
            sample_action,
            RuntimeError("CCM crash"),
            RiskTier.HIGH,
        )
        assert result.decision == ComplianceDecision.BLOCK
        assert len(result.violated_constraints) == 1
        assert "fail-closed" in result.details

    def test_fail_open_returns_flag(self, sample_action):
        result = apply_fail_safe(
            FailSafePolicy.FAIL_OPEN,
            sample_action,
            RuntimeError("CCM crash"),
            RiskTier.LOW,
        )
        assert result.decision == ComplianceDecision.FLAG
        assert "fail-open" in result.details


# ---------------------------------------------------------------------------
# 1c. Chaos tests — CCM crash in orchestrator
# ---------------------------------------------------------------------------


class _CrashingCCM(ConstraintComplianceMonitorBase):
    """A CCM that always raises RuntimeError."""

    def _domain_check(self, action, trajectory):
        raise RuntimeError("Simulated CCM crash!")


class _ScriptedAgent:
    """Agent that proposes a single action then finishes."""

    def generate(self, system_prompt, conversation, task):
        if not conversation:
            return AgentResponse(
                reasoning="I will execute a transfer.",
                tool_name="execute_transfer",
                tool_parameters={"amount_usd": 5000},
                is_final=False,
                raw_text="Executing transfer.",
            )
        return AgentResponse(
            reasoning="Done.",
            is_final=True,
            raw_text="Task complete.",
        )


class TestChaosFailClosed:
    """CCM crashes on HIGH risk → trajectory BLOCKED."""

    def test_high_risk_ccm_crash_blocks(self, db_engine):
        store = TrajectoryStore(db_engine)

        # Use a profiler that classifies everything as HIGH
        class _HighProfiler:
            def classify(self, task):
                from verifiable_observability.storage.models import StrategyProfile
                return StrategyProfile(
                    task_id=task.task_id,
                    domain=task.domain,
                    task_type="test",
                    risk_tier=RiskTier.HIGH,
                    expected_turn_range=(1, 5),
                    active_constraint_set_id="test",
                )

        orch = Orchestrator(
            strategy_profiler=_HighProfiler(),
            rule_bank=StubRuleBank(),
            ccm=_CrashingCCM(),
            agent_adapter=_ScriptedAgent(),
            trajectory_store=store,
        )
        task = Task(domain=Domain.FINANCE, description="Transfer $5000")
        traj = orch.run(task)

        assert traj.outcome == TrajectoryOutcome.BLOCKED
        assert "fail-closed" in traj.failure_reason.lower() or "CCM unavailable" in traj.failure_reason


class TestChaosFailOpen:
    """CCM crashes on LOW risk → trajectory continues (with audit)."""

    def test_low_risk_ccm_crash_continues(self, db_engine):
        store = TrajectoryStore(db_engine)

        class _LowProfiler:
            def classify(self, task):
                from verifiable_observability.storage.models import StrategyProfile
                return StrategyProfile(
                    task_id=task.task_id,
                    domain=task.domain,
                    task_type="test",
                    risk_tier=RiskTier.LOW,
                    expected_turn_range=(1, 5),
                    active_constraint_set_id="test",
                )

        orch = Orchestrator(
            strategy_profiler=_LowProfiler(),
            rule_bank=StubRuleBank(),
            ccm=_CrashingCCM(),
            agent_adapter=_ScriptedAgent(),
            trajectory_store=store,
        )
        task = Task(domain=Domain.FINANCE, description="Transfer $5000")
        traj = orch.run(task)

        # Should NOT be blocked — fail-open allows it
        assert traj.outcome in (TrajectoryOutcome.COMPLETED, TrajectoryOutcome.TRUNCATED)
        # But the constraint check should show FLAG
        flagged = [
            cc
            for t in traj.turns
            for cc in t.constraint_checks
            if cc.decision == ComplianceDecision.FLAG
        ]
        assert len(flagged) > 0


# ---------------------------------------------------------------------------
# Ticket integration with CCM
# ---------------------------------------------------------------------------


class TestCCMTicketIntegration:
    """CCM issues tickets on ALLOW when a TicketIssuer is provided."""

    def test_finance_ccm_issues_ticket_on_allow(self, sample_trajectory):
        issuer = TicketIssuer(secret=b"test-key-32-bytes-of-padding!!!!!")
        ccm = FinanceCCM(ticket_issuer=issuer)

        action = Action(tool_name="get_account_balance", parameters={})
        result = ccm.check(action, sample_trajectory, sequence_no=1)

        assert result.decision == ComplianceDecision.ALLOW
        assert result.execution_ticket is not None
        assert result.execution_ticket.sequence_no == 1

    def test_finance_ccm_no_ticket_on_block(self, sample_trajectory):
        issuer = TicketIssuer(secret=b"test-key-32-bytes-of-padding!!!!!")
        ccm = FinanceCCM(ticket_issuer=issuer)

        action = Action(tool_name="delete_account", parameters={})
        result = ccm.check(action, sample_trajectory, sequence_no=1)

        assert result.decision == ComplianceDecision.BLOCK
        assert result.execution_ticket is None

    def test_stub_ccm_no_ticket_without_issuer(self, sample_trajectory):
        ccm = StubCCM()
        action = Action(tool_name="anything", parameters={})
        result = ccm.check(action, sample_trajectory, sequence_no=1)

        assert result.decision == ComplianceDecision.ALLOW
        assert result.execution_ticket is None

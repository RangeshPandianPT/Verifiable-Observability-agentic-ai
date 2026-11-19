"""
Unit tests for RuleBank in-memory caching, check_explain diagnostics,
and CLI diagnostics/certificate export commands.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from verifiable_observability.cli.main import app
from verifiable_observability.core.matching import HybridMatcher, MatchDiagnostics
from verifiable_observability.core.rule_bank import RuleBank
from verifiable_observability.storage.db import RuleStore, TrajectoryStore, create_db_engine
from verifiable_observability.storage.models import (
    ActionPattern,
    Decision,
    Domain,
    ObservationPattern,
    Rule,
    VerificationStatus,
)


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_engine():
    return create_db_engine(":memory:")


@pytest.fixture
def rule_store(mem_engine):
    return RuleStore(mem_engine)


@pytest.fixture
def sample_rule():
    return Rule(
        rule_id="test-cache-rule-01",
        domain=Domain.FINANCE,
        name="test_rule",
        description="Rule for cache and explain testing",
        observation_pattern=ObservationPattern(
            domain=Domain.FINANCE,
            task_type="routine_transfer",
            numeric_conditions={"amount": {"lte": 1000.0}},
            reasoning_keywords=["transfer"],
        ),
        prescribed_action_pattern=ActionPattern(
            tool_name="execute_transfer",
            description="Prescribed transfer action",
        ),
    )


# ---------------------------------------------------------------------------
# 1. RuleBank Caching Tests
# ---------------------------------------------------------------------------


class TestRuleBankCache:
    def test_cache_population_and_invalidation_on_verify(self, rule_store, sample_rule):
        rb = RuleBank(rule_store, enable_cache=True)
        assert rb._cached_verified_rules is None

        # Add rule (PENDING)
        rb.add_rule(sample_rule)
        assert rb._cached_verified_rules is None

        # First query populates cache with 0 verified rules
        verified = rb._get_verified_rules()
        assert len(verified) == 0
        assert rb._cached_verified_rules is not None

        # Verifying rule invalidates cache
        rb.verify_rule(sample_rule.rule_id, verifier="tester")
        assert rb._cached_verified_rules is None

        # Next query repopulates cache with the verified rule
        verified = rb._get_verified_rules()
        assert len(verified) == 1
        assert verified[0].rule_id == sample_rule.rule_id

    def test_cache_invalidation_on_deprecate(self, rule_store, sample_rule):
        rb = RuleBank(rule_store, enable_cache=True)
        rb.add_rule(sample_rule)
        rb.verify_rule(sample_rule.rule_id, verifier="tester")

        # Populate cache
        assert len(rb._get_verified_rules()) == 1
        assert rb._cached_verified_rules is not None

        # Deprecating invalidates cache
        rb.deprecate_rule(sample_rule.rule_id, reason="Obsolete policy")
        assert rb._cached_verified_rules is None

        # Cache now empty of verified rules
        assert len(rb._get_verified_rules()) == 0

    def test_cache_disabled_mode(self, rule_store, sample_rule):
        rb = RuleBank(rule_store, enable_cache=False)
        rb.add_rule(sample_rule)
        rb.verify_rule(sample_rule.rule_id, verifier="tester")

        verified = rb._get_verified_rules()
        assert len(verified) == 1
        assert rb._cached_verified_rules is None


# ---------------------------------------------------------------------------
# 2. RuleBank check_explain & Custom Matcher Tests
# ---------------------------------------------------------------------------


class TestRuleBankCheckExplain:
    def test_check_explain_returns_diagnostics(self, rule_store, sample_rule):
        rb = RuleBank(rule_store)
        rb.add_rule(sample_rule)
        rb.verify_rule(sample_rule.rule_id, verifier="tester")

        decision = Decision(
            turn_index=0,
            reasoning="Initiating transfer for verified account.",
            observation_metadata={
                "domain": "finance",
                "task_type": "routine_transfer",
                "amount": 500.0,
            },
        )

        res, diag = rb.check_explain(decision)
        assert res.matched is True
        assert res.rule_id == sample_rule.rule_id
        assert isinstance(diag, MatchDiagnostics)
        assert diag.matched is True
        assert len(diag.passed_predicates) > 0

    def test_rule_bank_with_custom_hybrid_matcher(self, rule_store, sample_rule):
        hybrid = HybridMatcher(predicate_weight=0.7, similarity_weight=0.3)
        rb = RuleBank(rule_store, matcher=hybrid)
        rb.add_rule(sample_rule)
        rb.verify_rule(sample_rule.rule_id, verifier="tester")

        decision = Decision(
            turn_index=0,
            reasoning="Executing transfer per guidelines.",
            observation_metadata={
                "domain": "finance",
                "task_type": "routine_transfer",
                "amount": 500.0,
            },
        )

        res, diag = rb.check_explain(decision)
        assert res.matched is True
        assert diag is not None
        assert "HybridMatcher" in diag.method


# ---------------------------------------------------------------------------
# 3. CLI Diagnostics & Certificate Round-Trip Tests
# ---------------------------------------------------------------------------


class TestCLIFeatures:
    def test_vo_check_command(self, tmp_path):
        db_path = str(tmp_path / "test_check.db")
        result = runner.invoke(app, ["check", "--db", db_path])
        assert result.exit_code == 0
        assert "Verifiable Observability — System Diagnostics" in result.output
        assert "Runtime" in result.output
        assert "Cryptography" in result.output

    def test_vo_audit_cert_export_and_offline_verification(self, tmp_path):
        db_path = str(tmp_path / "test_audit.db")
        # Run demo on this db to seed a trajectory
        run_res = runner.invoke(app, ["demo", "--db", db_path])
        assert run_res.exit_code == 0

        # Export certificate to JSON
        cert_out = tmp_path / "exported_cert.json"
        export_res = runner.invoke(app, ["audit-cert", "--db", db_path, "--out", str(cert_out)])
        assert export_res.exit_code == 0
        assert cert_out.exists()

        cert_data = json.loads(cert_out.read_text(encoding="utf-8"))
        assert "certificate_id" in cert_data
        assert "signature" in cert_data
        assert "merkle_root" in cert_data

        # Verify certificate offline
        verify_res = runner.invoke(app, ["audit-cert", "--verify-file", str(cert_out)])
        assert verify_res.exit_code == 0
        assert "CERTIFICATE AUTHENTIC & CRYPTOGRAPHICALLY VALID" in verify_res.output

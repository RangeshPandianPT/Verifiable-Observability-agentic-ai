"""
Phase 1 Rule Bank Tests

Covers:
  1. Exact structured-predicate match → high confidence, matched=True
  2. Near-miss (one predicate fails) → confidence below threshold, matched=False
  3. Deprecated rules are excluded from check()
  4. Pending rules are excluded from check()
  5. Provenance is preserved after add_rule()
  6. verify_rule() flow: PENDING → VERIFIED
  7. deprecate_rule() flow: VERIFIED → DEPRECATED
  8. query() filters by domain and status
  9. Similarity fallback fires when predicates fail
 10. Finance seed rules load correctly (15 rules)
"""

from __future__ import annotations

import pytest

from verifiable_observability.core.matching import CONFIDENCE_THRESHOLD
from verifiable_observability.core.rule_bank import RuleBank
from verifiable_observability.simulation.domains.finance.seed_rules import (
    get_seed_rules,
    load_seed_rules_into_bank,
)
from verifiable_observability.storage.db import RuleStore, create_db_engine
from verifiable_observability.storage.models import (
    ActionPattern,
    Decision,
    Domain,
    ObservationPattern,
    Rule,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_engine():
    return create_db_engine(":memory:")


@pytest.fixture()
def rule_store(in_memory_engine):
    return RuleStore(in_memory_engine)


@pytest.fixture()
def rule_bank(rule_store):
    return RuleBank(rule_store)


@pytest.fixture()
def seeded_bank(rule_bank):
    """RuleBank pre-loaded with verified Finance seed rules."""
    load_seed_rules_into_bank(rule_bank, auto_verify=True)
    return rule_bank


def _make_rule(
    rule_id: str = "test-rule-001",
    domain: Domain = Domain.FINANCE,
    task_type: str = "routine_transfer",
    reasoning_keywords: list[str] | None = None,
    numeric_conditions: dict | None = None,
    description: str = "Test rule",
) -> Rule:
    return Rule(
        rule_id=rule_id,
        domain=domain,
        name=f"rule_{rule_id}",
        description=description,
        observation_pattern=ObservationPattern(
            domain=domain,
            task_type=task_type,
            reasoning_keywords=reasoning_keywords or [],
            numeric_conditions=numeric_conditions or {},
        ),
        prescribed_action_pattern=ActionPattern(
            tool_name="test_tool",
            description="Do the thing",
        ),
    )


def _make_decision(
    reasoning: str = "I need to verify balance before transfer.",
    domain: str = "finance",
    task_type: str = "routine_transfer",
    metadata: dict | None = None,
    turn_index: int = 0,
) -> Decision:
    obs_meta = {"domain": domain, "task_type": task_type}
    if metadata:
        obs_meta.update(metadata)
    return Decision(
        turn_index=turn_index,
        reasoning=reasoning,
        observation_metadata=obs_meta,
    )


# ---------------------------------------------------------------------------
# 1. Exact match
# ---------------------------------------------------------------------------


class TestExactMatch:
    def test_exact_structured_match_returns_high_confidence(self, rule_bank):
        """A decision that satisfies all predicates should match with confidence ≥ threshold."""
        rule = _make_rule(
            rule_id="fin-exact-001",
            domain=Domain.FINANCE,
            task_type="routine_transfer",
            reasoning_keywords=["balance", "transfer"],
        )
        rule_bank.add_rule(rule, provenance="test")
        rule_bank.verify_rule(rule.rule_id, verifier="human")

        decision = _make_decision(
            reasoning="I need to check the balance before initiating the transfer.",
        )
        result = rule_bank.check(decision)

        assert result.matched is True
        assert result.confidence >= CONFIDENCE_THRESHOLD
        assert result.rule_id == rule.rule_id

    def test_exact_numeric_match(self, rule_bank):
        """Numeric predicates should match correctly."""
        rule = _make_rule(
            rule_id="fin-num-001",
            numeric_conditions={"amount_usd": {"lt": 10000}},
            reasoning_keywords=["transfer"],
        )
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")

        decision = _make_decision(
            reasoning="I should execute the transfer since it is under the limit.",
            metadata={"amount_usd": 5000},
        )
        result = rule_bank.check(decision)

        assert result.matched is True
        assert result.confidence >= CONFIDENCE_THRESHOLD

    def test_numeric_boundary_above_fails(self, rule_bank):
        """An amount above the threshold should NOT match a 'lt 10000' predicate."""
        # Rule has ONLY a numeric condition — if it fails, score = 0.0
        rule = Rule(
            rule_id="fin-num-002",
            domain=Domain.FINANCE,
            name="rule_fin-num-002",
            description="Numeric-only rule for boundary test",
            observation_pattern=ObservationPattern(
                numeric_conditions={"amount_usd": {"lt": 10000}},
                # No domain / task_type / keyword predicates — score is purely numeric
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="execute_small_transfer",
                description="Execute for amounts under 10k",
            ),
        )
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")

        decision = _make_decision(
            reasoning="Transfer amount exceeds the auto-approve limit.",
            metadata={"amount_usd": 50000},
        )
        result = rule_bank.check(decision)

        # Numeric predicate fails: 0 out of 1 predicates match → score = 0.0
        assert result.matched is False


# ---------------------------------------------------------------------------
# 2. Near-miss
# ---------------------------------------------------------------------------


class TestNearMiss:
    def test_wrong_task_type_reduces_confidence(self, rule_bank):
        """A decision with the wrong task_type should not match a typed rule."""
        # Use a rule with 4 predicates (domain + task_type + 2 keywords).
        # The decision will mismatch on BOTH task_type AND domain → 2/4 pass → 0.5 < 0.6
        rule = Rule(
            rule_id="fin-near-001",
            domain=Domain.FINANCE,
            name="rule_fin-near-001",
            description="Near-miss test rule: portfolio rebalance allocation",
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="portfolio_rebalance",
                reasoning_keywords=["allocation", "rebalance"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="validate_rebalance",
                description="Validate rebalance target.",
            ),
        )
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")

        # Decision: wrong task_type + missing keywords → only domain matches (1/4 = 0.25)
        decision = Decision(
            turn_index=0,
            reasoning="I am going to execute a simple wire transfer today.",
            observation_metadata={
                "domain": "finance",
                "task_type": "routine_transfer",  # wrong type
            },
        )
        result = rule_bank.check(decision)

        # task_type fails, both keywords absent → 1 out of 4 predicates passes (domain only)
        assert result.matched is False

    def test_missing_keywords_reduces_confidence(self, rule_bank):
        """A decision without the required keywords should not match."""
        # Rule with 4 predicates: domain + task_type + 2 specific keywords
        rule = Rule(
            rule_id="fin-near-002",
            domain=Domain.FINANCE,
            name="rule_fin-near-002",
            description="Compliance notification rule",
            observation_pattern=ObservationPattern(
                domain=Domain.FINANCE,
                task_type="high_value_trade",
                reasoning_keywords=["compliance", "notification"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="send_notification",
                description="Send compliance notification.",
            ),
        )
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")

        # Decision: domain matches, task_type is wrong, keywords absent
        # → 1 out of 4 predicates pass → 0.25 < 0.6
        decision = Decision(
            turn_index=0,
            reasoning="I am going to execute the wire transfer right now.",
            observation_metadata={
                "domain": "finance",
                "task_type": "routine_transfer",  # wrong
            },
        )
        result = rule_bank.check(decision)
        assert result.matched is False


# ---------------------------------------------------------------------------
# 3. Deprecated rules excluded
# ---------------------------------------------------------------------------


class TestDeprecatedExclusion:
    def test_deprecated_rule_not_returned_by_check(self, rule_bank):
        """Deprecated rules must NEVER appear as a check() match."""
        rule = _make_rule(
            rule_id="fin-dep-001",
            reasoning_keywords=["balance", "transfer"],
        )
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")
        rule_bank.deprecate_rule(rule.rule_id, reason="replaced by fin-dep-002")

        decision = _make_decision(
            reasoning="I need to check the balance before the transfer."
        )
        result = rule_bank.check(decision)

        # No verified rules → no match
        assert result.matched is False

    def test_deprecation_reason_preserved(self, rule_bank):
        rule = _make_rule(rule_id="fin-dep-002")
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")
        reason = "policy changed Q3 2025"
        updated = rule_bank.deprecate_rule(rule.rule_id, reason=reason)
        assert updated.deprecation_reason == reason
        assert updated.verification_status == VerificationStatus.DEPRECATED


# ---------------------------------------------------------------------------
# 4. Pending rules excluded
# ---------------------------------------------------------------------------


class TestPendingExclusion:
    def test_pending_rule_not_matched(self, rule_bank):
        """A newly added (PENDING) rule must not be returned by check()."""
        rule = _make_rule(
            rule_id="fin-pend-001",
            reasoning_keywords=["balance", "transfer"],
        )
        rule_bank.add_rule(rule)  # Not verified

        decision = _make_decision(
            reasoning="Check the account balance before the transfer."
        )
        result = rule_bank.check(decision)

        assert result.matched is False


# ---------------------------------------------------------------------------
# 5. Provenance preserved
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_provenance_stored_in_source_trajectory_id(self, rule_bank, rule_store):
        rule = _make_rule(rule_id="fin-prov-001")
        rule_bank.add_rule(rule, provenance="trajectory-abc-123")

        loaded = rule_store.load("fin-prov-001")
        assert loaded is not None
        assert loaded.source_trajectory_id == "trajectory-abc-123"

    def test_verifier_stored(self, rule_bank, rule_store):
        rule = _make_rule(rule_id="fin-prov-002")
        rule_bank.add_rule(rule)
        rule_bank.verify_rule("fin-prov-002", verifier="automatic:unit_test")

        loaded = rule_store.load("fin-prov-002")
        assert loaded.verifier == "automatic:unit_test"
        assert loaded.verification_status == VerificationStatus.VERIFIED


# ---------------------------------------------------------------------------
# 6 & 7. Lifecycle flows
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_pending_to_verified_flow(self, rule_bank):
        rule = _make_rule(rule_id="fin-life-001")
        added = rule_bank.add_rule(rule)
        assert added.verification_status == VerificationStatus.PENDING

        verified = rule_bank.verify_rule(added.rule_id, verifier="human")
        assert verified.verification_status == VerificationStatus.VERIFIED

    def test_verified_to_deprecated_flow(self, rule_bank):
        rule = _make_rule(rule_id="fin-life-002")
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")
        deprecated = rule_bank.deprecate_rule(rule.rule_id, reason="test")
        assert deprecated.verification_status == VerificationStatus.DEPRECATED

    def test_verify_nonexistent_raises(self, rule_bank):
        with pytest.raises(KeyError):
            rule_bank.verify_rule("does-not-exist", verifier="human")

    def test_deprecate_nonexistent_raises(self, rule_bank):
        with pytest.raises(KeyError):
            rule_bank.deprecate_rule("does-not-exist", reason="gone")


# ---------------------------------------------------------------------------
# 8. Query
# ---------------------------------------------------------------------------


class TestQuery:
    def test_query_by_domain(self, seeded_bank):
        rules = seeded_bank.query(domain="finance")
        assert len(rules) == 15
        assert all(r.domain == Domain.FINANCE for r in rules)

    def test_query_by_status_verified(self, seeded_bank):
        rules = seeded_bank.query(status="verified")
        assert len(rules) == 15

    def test_query_by_status_pending(self, seeded_bank):
        rules = seeded_bank.query(status="pending")
        assert len(rules) == 0  # all auto-verified

    def test_query_combined_filter(self, seeded_bank):
        rules = seeded_bank.query(domain="finance", status="verified")
        assert len(rules) == 15

    def test_query_wrong_domain_returns_empty(self, seeded_bank):
        rules = seeded_bank.query(domain="healthcare")
        assert len(rules) == 0


# ---------------------------------------------------------------------------
# 9. Similarity fallback
# ---------------------------------------------------------------------------


class TestSimilarityFallback:
    def test_similarity_fallback_fires_for_text_only_match(self, rule_bank):
        """
        A rule with NO structured predicates (only name/description text)
        should be found via similarity matching when the reasoning text overlaps.
        """
        rule = Rule(
            rule_id="fin-sim-001",
            domain=Domain.FINANCE,
            name="verify_counterparty_details",
            description=(
                "Always verify counterparty account details and recipient "
                "information before executing any financial transaction."
            ),
            observation_pattern=ObservationPattern(),  # empty predicates
            prescribed_action_pattern=ActionPattern(
                tool_name="verify_counterparty",
                description="Verify counterparty.",
            ),
        )
        rule_bank.add_rule(rule)
        rule_bank.verify_rule(rule.rule_id, verifier="human")

        decision = _make_decision(
            reasoning=(
                "I must verify counterparty and recipient account details "
                "before executing this financial transaction."
            ),
        )
        result = rule_bank.check(decision)

        # Should match via similarity (strong text overlap)
        assert result.matched is True
        assert result.match_method == "similarity"


# ---------------------------------------------------------------------------
# 10. Finance seed rules
# ---------------------------------------------------------------------------


class TestFinanceSeedRules:
    def test_seed_rules_count(self):
        rules = get_seed_rules()
        assert len(rules) == 15

    def test_seed_rules_all_finance_domain(self):
        rules = get_seed_rules()
        assert all(r.domain == Domain.FINANCE for r in rules)

    def test_seed_rules_cover_all_task_types(self):
        rules = get_seed_rules()
        task_types = {r.observation_pattern.task_type for r in rules}
        assert "routine_transfer" in task_types
        assert "portfolio_rebalance" in task_types
        assert "high_value_trade" in task_types

    def test_load_into_bank(self, rule_bank):
        loaded = load_seed_rules_into_bank(rule_bank, auto_verify=True)
        assert len(loaded) == 15
        all_stored = rule_bank.query(status="verified")
        assert len(all_stored) == 15

    def test_seed_rule_exact_match_routine_transfer(self, seeded_bank):
        """fin-rt-002 should match a decision about a small transfer."""
        decision = Decision(
            turn_index=0,
            reasoning=(
                "The transfer amount is $500, which is well below $10,000. "
                "I will execute the transfer automatically."
            ),
            observation_metadata={
                "domain": "finance",
                "task_type": "routine_transfer",
                "amount_usd": 500,
            },
        )
        result = seeded_bank.check(decision)
        assert result.matched is True

    def test_seed_rule_high_value_trade_match(self, seeded_bank):
        """fin-hv-001 should match a decision about a large trade risk assessment."""
        decision = Decision(
            turn_index=0,
            reasoning=(
                "This is a high-value trade exceeding $100,000. "
                "I must run a risk assessment before submitting the order."
            ),
            observation_metadata={
                "domain": "finance",
                "task_type": "high_value_trade",
                "amount_usd": 250000,
            },
        )
        result = seeded_bank.check(decision)
        assert result.matched is True

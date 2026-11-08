"""
Tests for Advanced Rule Matching Engine and Top-Level Package Exports.

Covers:
  1. Top-Level Package Public API exports and version integrity.
  2. StructuredPredicateMatcher advanced operators:
     - ne (not equal)
     - between [min, max]
     - in / not_in sets
     - Negative reasoning keywords (!forbidden, -unauthorized)
     - Regex reasoning patterns (regex:pattern)
     - Regex & substring metadata conditions (regex:, contains:, prefix:, suffix:)
     - Dict metadata conditions (in, not_in, ne)
  3. MatchDiagnostics audit trail generation via match_explain().
  4. SimilarityMatcher stopword filtering, token overlap diagnostics, and fallback.
  5. HybridMatcher structured-semantic blending, decisive short-circuits, and floor enforcement.
"""

from __future__ import annotations

import pytest

import verifiable_observability as vo
from verifiable_observability.core.matching import (
    CONFIDENCE_THRESHOLD,
    HybridMatcher,
    MatchDiagnostics,
    SimilarityMatcher,
    StructuredPredicateMatcher,
)
from verifiable_observability.storage.models import (
    ActionPattern,
    Decision,
    Domain,
    ObservationPattern,
    Rule,
)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def _make_rule(
    rule_id: str = "adv-rule-001",
    domain: Domain = Domain.FINANCE,
    task_type: str = "asset_transfer",
    numeric_conditions: dict | None = None,
    reasoning_keywords: list[str] | None = None,
    metadata_conditions: dict | None = None,
    description: str = "Advanced test rule",
) -> Rule:
    return Rule(
        rule_id=rule_id,
        domain=domain,
        name=f"Rule {rule_id}",
        description=description,
        observation_pattern=ObservationPattern(
            domain=domain,
            task_type=task_type,
            numeric_conditions=numeric_conditions or {},
            reasoning_keywords=reasoning_keywords or [],
            metadata_conditions=metadata_conditions or {},
        ),
        prescribed_action_pattern=ActionPattern(
            tool_name="execute_transfer",
            description="Prescribed transfer action",
        ),
    )


def _make_decision(
    reasoning: str = "Authorizing transaction after compliance check.",
    domain: str = "finance",
    task_type: str = "asset_transfer",
    metadata: dict | None = None,
) -> Decision:
    obs = {"domain": domain, "task_type": task_type}
    if metadata:
        obs.update(metadata)
    return Decision(
        turn_index=0,
        reasoning=reasoning,
        observation_metadata=obs,
    )


# ---------------------------------------------------------------------------
# 1. Top-Level Package Exports
# ---------------------------------------------------------------------------


class TestPackageExports:
    def test_version_present(self):
        assert hasattr(vo, "__version__")
        assert isinstance(vo.__version__, str)

    def test_core_classes_exported(self):
        expected_symbols = [
            "Orchestrator",
            "StrategyProfiler",
            "RuleBank",
            "StructuredPredicateMatcher",
            "SimilarityMatcher",
            "HybridMatcher",
            "MatchDiagnostics",
            "CONFIDENCE_THRESHOLD",
            "build_ccm",
            "AuditVerifier",
            "ComplianceCertificate",
            "BasicMetricsEngine",
            "Task",
            "Domain",
            "RiskTier",
            "Trajectory",
            "Rule",
        ]
        for sym in expected_symbols:
            assert hasattr(vo, sym), f"Missing public export: {sym}"
            assert sym in vo.__all__, f"Symbol {sym} missing from __all__"


# ---------------------------------------------------------------------------
# 2. Structured Predicate Matcher: Advanced Operators
# ---------------------------------------------------------------------------


class TestStructuredPredicateMatcherAdvanced:
    def setup_method(self):
        self.matcher = StructuredPredicateMatcher()

    def test_numeric_ne_operator(self):
        rule = _make_rule(
            numeric_conditions={"transfer_amount": {"ne": 0.0, "lte": 50000.0}}
        )
        dec_valid = _make_decision(metadata={"transfer_amount": 2500.0})
        dec_zero = _make_decision(metadata={"transfer_amount": 0.0})

        assert self.matcher.match(rule, dec_valid) == 1.0
        assert self.matcher.match(rule, dec_zero) < 1.0

    def test_numeric_between_operator(self):
        rule = _make_rule(
            numeric_conditions={"transfer_amount": {"between": [100.0, 5000.0]}}
        )
        dec_in = _make_decision(metadata={"transfer_amount": 2500.0})
        dec_low = _make_decision(metadata={"transfer_amount": 50.0})
        dec_high = _make_decision(metadata={"transfer_amount": 10000.0})

        assert self.matcher.match(rule, dec_in) == 1.0
        assert self.matcher.match(rule, dec_low) < 1.0
        assert self.matcher.match(rule, dec_high) < 1.0

    def test_numeric_in_and_not_in_operators(self):
        rule_in = _make_rule(
            numeric_conditions={"tier_code": {"in": [1, 2, 5]}}
        )
        rule_not_in = _make_rule(
            numeric_conditions={"tier_code": {"not_in": [0, 99]}}
        )

        dec_allowed = _make_decision(metadata={"tier_code": 2})
        dec_disallowed = _make_decision(metadata={"tier_code": 4})

        assert self.matcher.match(rule_in, dec_allowed) == 1.0
        assert self.matcher.match(rule_in, dec_disallowed) < 1.0

        assert self.matcher.match(rule_not_in, dec_allowed) == 1.0
        dec_banned = _make_decision(metadata={"tier_code": 99})
        assert self.matcher.match(rule_not_in, dec_banned) < 1.0

    def test_negative_reasoning_keywords(self):
        # Reasoning must contain "verify" and must NOT contain "bypass" or "ignore"
        rule = _make_rule(
            reasoning_keywords=["verify", "!bypass", "-ignore"]
        )
        dec_clean = _make_decision(reasoning="I will verify account balance before proceeding.")
        dec_tainted = _make_decision(reasoning="I will verify balance and bypass authentication.")

        assert self.matcher.match(rule, dec_clean) == 1.0
        assert self.matcher.match(rule, dec_tainted) < 1.0

    def test_regex_reasoning_keywords(self):
        rule = _make_rule(
            reasoning_keywords=["regex:acc_[0-9]{4}"]
        )
        dec_match = _make_decision(reasoning="Confirmed destination acc_4821.")
        dec_nomatch = _make_decision(reasoning="Confirmed destination without account id.")

        assert self.matcher.match(rule, dec_match) == 1.0
        assert self.matcher.match(rule, dec_nomatch) < 1.0

    def test_metadata_regex_and_substring_conditions(self):
        rule = _make_rule(
            metadata_conditions={
                "dest_account": "regex:^NL\\d{2}[A-Z]{4}\\d{10}$",
                "notes": "contains:urgent",
                "channel": "prefix:api_",
            }
        )
        dec_ok = _make_decision(
            metadata={
                "dest_account": "NL91ABNA0417164300",
                "notes": "Please process urgent transfer",
                "channel": "api_v2_partner",
            }
        )
        dec_fail = _make_decision(
            metadata={
                "dest_account": "US12345",
                "notes": "Standard transfer",
                "channel": "web_portal",
            }
        )

        assert self.matcher.match(rule, dec_ok) == 1.0
        assert self.matcher.match(rule, dec_fail) < 1.0

    def test_metadata_dict_conditions(self):
        rule = _make_rule(
            metadata_conditions={
                "user_role": {"in": ["auditor", "compliance_officer"]},
                "status": {"ne": "revoked"},
            }
        )
        dec_pass = _make_decision(metadata={"user_role": "auditor", "status": "active"})
        dec_fail_role = _make_decision(metadata={"user_role": "guest", "status": "active"})
        dec_fail_revoked = _make_decision(metadata={"user_role": "auditor", "status": "revoked"})

        assert self.matcher.match(rule, dec_pass) == 1.0
        assert self.matcher.match(rule, dec_fail_role) < 1.0
        assert self.matcher.match(rule, dec_fail_revoked) < 1.0

    def test_match_explain_diagnostics(self):
        rule = _make_rule(
            numeric_conditions={"amount": {"lte": 1000.0}},
            reasoning_keywords=["verify", "!skip"],
        )
        dec = _make_decision(
            reasoning="We should verify the target user.",
            metadata={"amount": 500.0},
        )
        diag = self.matcher.match_explain(rule, dec)

        assert isinstance(diag, MatchDiagnostics)
        assert diag.matched is True
        assert diag.score == 1.0
        assert len(diag.passed_predicates) > 0
        assert len(diag.failed_predicates) == 0
        assert "passed_count" in diag.details
        diag_dict = diag.to_dict()
        assert diag_dict["matched"] is True


# ---------------------------------------------------------------------------
# 3. Similarity Matcher: Stopwords & Normalization
# ---------------------------------------------------------------------------


class TestSimilarityMatcherAdvanced:
    def setup_method(self):
        self.matcher = SimilarityMatcher(filter_stopwords=True)

    def test_stopword_filtering(self):
        rule = _make_rule(
            description="Rebalance client investment portfolio according to asset allocation guidelines."
        )
        # Decision has key domain tokens matching the rule
        dec_informative = _make_decision(
            reasoning="Checking investment portfolio to rebalance asset allocation according to risk policy."
        )
        # Decision has lots of stopwords with no domain overlap
        dec_grammatical = _make_decision(
            reasoning="This is it and we should have been doing that because it was there for them."
        )

        score_inf = self.matcher.match(rule, dec_informative)
        score_gram = self.matcher.match(rule, dec_grammatical)

        assert score_inf > score_gram
        assert score_inf > 0.4
        assert score_gram == 0.0

    def test_stopword_only_fallback(self):
        rule = _make_rule(description="Do it now")
        dec = _make_decision(reasoning="Do it now")
        diag = self.matcher.match_explain(rule, dec)
        assert diag.score > 0.0

    def test_similarity_match_explain(self):
        rule = _make_rule(description="Liquidity check for treasury account.")
        dec = _make_decision(reasoning="Checking treasury liquidity metrics.")
        diag = self.matcher.match_explain(rule, dec)

        assert isinstance(diag, MatchDiagnostics)
        assert "overlap_token_count" in diag.details
        assert diag.details["overlap_token_count"] > 0


# ---------------------------------------------------------------------------
# 4. Hybrid Matcher: Blended Verification
# ---------------------------------------------------------------------------


class TestHybridMatcher:
    def test_exact_structured_match_short_circuits_to_one(self):
        hybrid = HybridMatcher(predicate_weight=0.7, similarity_weight=0.3)
        rule = _make_rule(numeric_conditions={"limit": {"gte": 100.0}})
        dec = _make_decision(metadata={"limit": 200.0}, reasoning="Completely unrelated prose.")

        diag = hybrid.match_explain(rule, dec)
        assert diag.score == 1.0
        assert diag.matched is True
        assert "Decisive Structured" in diag.method

    def test_structured_floor_rejection(self):
        hybrid = HybridMatcher(predicate_weight=0.7, similarity_weight=0.3, min_predicate_score=0.5)
        # Wrong domain and task type -> structured score 0.0
        rule = _make_rule(
            domain=Domain.HEALTHCARE,
            task_type="patient_lookup",
            description="High risk clinical action.",
        )
        dec = _make_decision(
            domain="finance",
            task_type="asset_transfer",
            reasoning="High risk clinical action with deep patient lookup.",
        )

        diag = hybrid.match_explain(rule, dec)
        assert diag.score < 0.5
        assert diag.matched is False
        assert "Floor" in diag.method

    def test_blended_match_boosts_borderline_rule(self):
        hybrid = HybridMatcher(predicate_weight=0.6, similarity_weight=0.4, min_predicate_score=0.3)
        # Rule with 3 predicates: domain (match), task_type (match), numeric (fail) -> p_score = 2/3 = 0.67
        rule = _make_rule(
            domain=Domain.FINANCE,
            task_type="asset_transfer",
            numeric_conditions={"transfer_amount": {"lt": 500.0}},
            description="Initiate institutional wire transfer with strict supervisory approval.",
        )
        dec = _make_decision(
            domain="finance",
            task_type="asset_transfer",
            metadata={"transfer_amount": 1000.0},
            reasoning="Initiate institutional wire transfer with supervisory approval for high volume.",
        )

        diag = hybrid.match_explain(rule, dec)
        assert diag.matched is True
        assert "predicate_score" in diag.details
        assert "similarity_score" in diag.details

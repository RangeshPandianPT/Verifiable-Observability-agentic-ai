"""
Rule Bank — auditable, versioned store of observation→action mappings.

Phase 0: StubRuleBank (pass-through, always matched).
Phase 1: Full RuleBank with:
  - structured predicate + similarity matching
  - add / verify / deprecate lifecycle
  - query API for CLI/inspection
  - high-performance in-memory caching of verified rules
  - pluggable custom matchers (e.g. HybridMatcher)
  - check_explain() returning transparent MatchDiagnostics

The Rule Bank only returns verified rules as matches. Pending and deprecated
rules are still stored (full audit trail) but excluded from check() results.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from verifiable_observability.core.matching import (
    CONFIDENCE_THRESHOLD,
    MatchDiagnostics,
    MatcherBase,
    SimilarityMatcher,
    StructuredPredicateMatcher,
)
from verifiable_observability.storage.db import RuleStore
from verifiable_observability.storage.models import (
    Decision,
    Rule,
    RuleCheckResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class RuleBankBase(ABC):
    """Abstract interface for the Rule Bank."""

    @abstractmethod
    def add_rule(self, rule: Rule, provenance: str = "") -> Rule:
        """
        Add a new rule. Default verification_status is PENDING.

        Args:
            rule:       The Rule to add (rule_id generated if not set).
            provenance: Free-text provenance note (e.g. trajectory_id, analyst).

        Returns:
            The stored Rule (with any server-side defaults applied).
        """
        ...

    @abstractmethod
    def verify_rule(self, rule_id: str, verifier: str) -> Rule:
        """
        Promote a PENDING rule to VERIFIED.

        Args:
            rule_id:  Target rule's ID.
            verifier: "human" or "automatic:<method>".

        Returns:
            Updated Rule.
        """
        ...

    @abstractmethod
    def deprecate_rule(self, rule_id: str, reason: str) -> Rule:
        """Mark a rule DEPRECATED (excluded from check())."""
        ...

    @abstractmethod
    def check(self, decision: Decision) -> RuleCheckResult:
        """
        Check a Decision against the Rule Bank.

        Args:
            decision: The agent's reasoning step to evaluate.

        Returns:
            RuleCheckResult with matched, rule_id, confidence, match_method.
        """
        ...

    def check_explain(
        self, decision: Decision
    ) -> tuple[RuleCheckResult, MatchDiagnostics | None]:
        """
        Check a Decision against the Rule Bank and return detailed diagnostics.
        Default implementation delegates to check().
        """
        return self.check(decision), None

    @abstractmethod
    def query(
        self,
        domain: str | None = None,
        status: str | None = None,
    ) -> list[Rule]:
        """Return rules filtered by domain and/or status."""
        ...


# ---------------------------------------------------------------------------
# Phase 0: stub
# ---------------------------------------------------------------------------


class StubRuleBank(RuleBankBase):
    """
    Pass-through stub — always reports matched=True with confidence=1.0.

    Used in Phase 0 smoke tests only. Not backed by any storage.
    """

    _rules: list[Rule] = []

    def add_rule(self, rule: Rule, provenance: str = "") -> Rule:
        self._rules.append(rule)
        return rule

    def verify_rule(self, rule_id: str, verifier: str) -> Rule:
        for r in self._rules:
            if r.rule_id == rule_id:
                return r
        raise KeyError(f"Rule {rule_id} not found")

    def deprecate_rule(self, rule_id: str, reason: str) -> Rule:
        for r in self._rules:
            if r.rule_id == rule_id:
                return r
        raise KeyError(f"Rule {rule_id} not found")

    def check(self, decision: Decision) -> RuleCheckResult:
        return RuleCheckResult(
            decision_id=decision.decision_id,
            matched=True,
            rule_id=None,
            rule_name="stub",
            confidence=1.0,
            match_method="stub",
        )

    def check_explain(
        self, decision: Decision
    ) -> tuple[RuleCheckResult, MatchDiagnostics | None]:
        return self.check(decision), None

    def query(
        self,
        domain: str | None = None,
        status: str | None = None,
    ) -> list[Rule]:
        return list(self._rules)


# ---------------------------------------------------------------------------
# Phase 1: full implementation
# ---------------------------------------------------------------------------


class RuleBank(RuleBankBase):
    """
    Full Rule Bank implementation.

    Matching strategy:
      1. If custom matcher provided, use custom matcher (e.g. HybridMatcher).
      2. Otherwise, structured predicate matching evaluated for all VERIFIED rules.
      3. If no rule exceeds CONFIDENCE_THRESHOLD via predicates, fall back to
         similarity matching.
      4. Best match is returned; if best score < CONFIDENCE_THRESHOLD, no match.

    Performance:
      Maintains an in-memory cache of VERIFIED rules that auto-invalidates
      on rule additions, verifications, or deprecations.

    Only VERIFIED rules are returned as matches. PENDING and DEPRECATED rules
    are excluded from check() but preserved for audit via query().
    """

    def __init__(
        self,
        rule_store: RuleStore,
        matcher: MatcherBase | None = None,
        enable_cache: bool = True,
    ) -> None:
        self._store = rule_store
        self._predicate_matcher = StructuredPredicateMatcher()
        self._similarity_matcher = SimilarityMatcher()
        self._custom_matcher = matcher
        self._enable_cache = enable_cache
        self._cached_verified_rules: list[Rule] | None = None

    def _invalidate_cache(self) -> None:
        self._cached_verified_rules = None

    def _get_verified_rules(self) -> list[Rule]:
        if self._enable_cache and self._cached_verified_rules is not None:
            return self._cached_verified_rules
        rules = self._store.list_rules(status=VerificationStatus.VERIFIED.value)
        if self._enable_cache:
            self._cached_verified_rules = rules
        return rules

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    def add_rule(self, rule: Rule, provenance: str = "") -> Rule:
        """Insert a new rule with PENDING status."""
        rule.verification_status = VerificationStatus.PENDING
        if provenance and not rule.source_trajectory_id:
            rule.source_trajectory_id = provenance
        self._store.save(rule)
        self._invalidate_cache()
        logger.info(
            "Rule added: %s (%s) [%s]",
            rule.rule_id[:8],
            rule.name,
            rule.verification_status.value,
        )
        return rule

    def verify_rule(self, rule_id: str, verifier: str) -> Rule:
        """Promote PENDING → VERIFIED."""
        rule = self._load_or_raise(rule_id)
        if rule.verification_status == VerificationStatus.DEPRECATED:
            raise ValueError(f"Cannot verify a deprecated rule: {rule_id}")
        rule.verification_status = VerificationStatus.VERIFIED
        rule.verifier = verifier
        rule.updated_at = datetime.now(timezone.utc)
        self._store.save(rule)
        self._invalidate_cache()
        logger.info("Rule verified: %s by %s", rule_id[:8], verifier)
        return rule

    def deprecate_rule(self, rule_id: str, reason: str) -> Rule:
        """Mark a rule as DEPRECATED (excluded from future checks)."""
        rule = self._load_or_raise(rule_id)
        rule.verification_status = VerificationStatus.DEPRECATED
        rule.deprecation_reason = reason
        rule.updated_at = datetime.now(timezone.utc)
        self._store.save(rule)
        self._invalidate_cache()
        logger.info("Rule deprecated: %s — %s", rule_id[:8], reason)
        return rule

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def check(self, decision: Decision) -> RuleCheckResult:
        """
        Find the best matching VERIFIED rule for this decision.

        Returns RuleCheckResult with:
          matched=True  if best confidence >= CONFIDENCE_THRESHOLD
          matched=False otherwise
        """
        res, _ = self.check_explain(decision)
        return res

    def check_explain(
        self, decision: Decision
    ) -> tuple[RuleCheckResult, MatchDiagnostics | None]:
        """
        Find the best matching VERIFIED rule and return both the RuleCheckResult
        and the full MatchDiagnostics breakdown explaining why it matched or failed.
        """
        verified_rules = self._get_verified_rules()

        if not verified_rules:
            result = RuleCheckResult(
                decision_id=decision.decision_id,
                matched=False,
                confidence=0.0,
                match_method="none",
            )
            return result, None

        best_rule: Rule | None = None
        best_score = 0.0
        best_method = "none"
        best_diagnostics: MatchDiagnostics | None = None

        if self._custom_matcher is not None:
            for rule in verified_rules:
                diag = self._custom_matcher.match_explain(rule, decision)
                if diag.score > best_score:
                    best_score = diag.score
                    best_rule = rule
                    best_method = diag.method
                    best_diagnostics = diag
        else:
            # Pass 1: structured predicate matching
            best_rule, best_score, best_method, best_diagnostics = self._best_structured_explain(
                verified_rules, decision
            )

            # Pass 2: similarity fallback if nothing crossed threshold
            if best_score < CONFIDENCE_THRESHOLD:
                sim_rule, sim_score, sim_diag = self._best_similarity_explain(
                    verified_rules, decision
                )
                if sim_score > best_score:
                    best_rule = sim_rule
                    best_score = sim_score
                    best_method = "similarity"
                    best_diagnostics = sim_diag

        if best_score >= CONFIDENCE_THRESHOLD and best_rule is not None:
            return (
                RuleCheckResult(
                    decision_id=decision.decision_id,
                    matched=True,
                    rule_id=best_rule.rule_id,
                    rule_name=best_rule.name,
                    confidence=round(best_score, 4),
                    match_method=best_method,
                ),
                best_diagnostics,
            )

        return (
            RuleCheckResult(
                decision_id=decision.decision_id,
                matched=False,
                confidence=round(best_score, 4),
                match_method="none",
            ),
            best_diagnostics,
        )

    def _best_structured(
        self, rules: list[Rule], decision: Decision
    ) -> tuple[Rule | None, float, str]:
        rule, score, method, _ = self._best_structured_explain(rules, decision)
        return rule, score, method

    def _best_structured_explain(
        self, rules: list[Rule], decision: Decision
    ) -> tuple[Rule | None, float, str, MatchDiagnostics | None]:
        best_rule: Rule | None = None
        best_score = 0.0
        best_diag: MatchDiagnostics | None = None
        for rule in rules:
            diag = self._predicate_matcher.match_explain(rule, decision)
            if diag.score > best_score:
                best_score = diag.score
                best_rule = rule
                best_diag = diag
        return best_rule, best_score, "structured_predicate", best_diag

    def _best_similarity(
        self, rules: list[Rule], decision: Decision
    ) -> tuple[Rule | None, float]:
        rule, score, _ = self._best_similarity_explain(rules, decision)
        return rule, score

    def _best_similarity_explain(
        self, rules: list[Rule], decision: Decision
    ) -> tuple[Rule | None, float, MatchDiagnostics | None]:
        best_rule: Rule | None = None
        best_score = 0.0
        best_diag: MatchDiagnostics | None = None
        for rule in rules:
            diag = self._similarity_matcher.match_explain(rule, decision)
            if diag.score > best_score:
                best_score = diag.score
                best_rule = rule
                best_diag = diag
        return best_rule, best_score, best_diag

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        domain: str | None = None,
        status: str | None = None,
    ) -> list[Rule]:
        return self._store.list_rules(domain=domain, status=status)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_or_raise(self, rule_id: str) -> Rule:
        rule = self._store.load(rule_id)
        if rule is None:
            raise KeyError(f"Rule not found: {rule_id}")
        return rule

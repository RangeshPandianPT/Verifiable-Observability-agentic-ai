"""
Rule Bank — auditable, versioned store of observation→action mappings.

Phase 0: StubRuleBank (pass-through, always matched).
Phase 1: Full RuleBank with:
  - structured predicate + similarity matching
  - add / verify / deprecate lifecycle
  - query API for CLI/inspection

The Rule Bank only returns verified rules as matches. Pending and deprecated
rules are still stored (full audit trail) but excluded from check() results.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from verifiable_observability.core.matching import (
    CONFIDENCE_THRESHOLD,
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
        Add a new rule.  Default verification_status is PENDING.

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
      1. Structured predicate matching (StructuredPredicateMatcher) — evaluated
         for all VERIFIED rules.
      2. If no rule exceeds CONFIDENCE_THRESHOLD via predicates, fall back to
         TF-IDF cosine similarity (SimilarityMatcher).
      3. Best match is returned; if best score < CONFIDENCE_THRESHOLD, no match.

    Only VERIFIED rules are returned as matches. PENDING and DEPRECATED rules
    are excluded from check() but preserved for audit via query().
    """

    def __init__(self, rule_store: RuleStore) -> None:
        self._store = rule_store
        self._predicate_matcher = StructuredPredicateMatcher()
        self._similarity_matcher = SimilarityMatcher()

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    def add_rule(self, rule: Rule, provenance: str = "") -> Rule:
        """Insert a new rule with PENDING status."""
        rule.verification_status = VerificationStatus.PENDING
        if provenance and not rule.source_trajectory_id:
            rule.source_trajectory_id = provenance
        self._store.save(rule)
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
        logger.info("Rule verified: %s by %s", rule_id[:8], verifier)
        return rule

    def deprecate_rule(self, rule_id: str, reason: str) -> Rule:
        """Mark a rule as DEPRECATED (excluded from future checks)."""
        rule = self._load_or_raise(rule_id)
        rule.verification_status = VerificationStatus.DEPRECATED
        rule.deprecation_reason = reason
        rule.updated_at = datetime.now(timezone.utc)
        self._store.save(rule)
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
        verified_rules = self._store.list_rules(status=VerificationStatus.VERIFIED.value)

        if not verified_rules:
            return RuleCheckResult(
                decision_id=decision.decision_id,
                matched=False,
                confidence=0.0,
                match_method="none",
            )

        # Pass 1: structured predicate matching
        best_rule, best_score, best_method = self._best_structured(
            verified_rules, decision
        )

        # Pass 2: similarity fallback if nothing crossed threshold
        if best_score < CONFIDENCE_THRESHOLD:
            sim_rule, sim_score = self._best_similarity(verified_rules, decision)
            if sim_score > best_score:
                best_rule, best_score, best_method = sim_rule, sim_score, "similarity"

        if best_score >= CONFIDENCE_THRESHOLD and best_rule is not None:
            return RuleCheckResult(
                decision_id=decision.decision_id,
                matched=True,
                rule_id=best_rule.rule_id,
                rule_name=best_rule.name,
                confidence=round(best_score, 4),
                match_method=best_method,
            )

        return RuleCheckResult(
            decision_id=decision.decision_id,
            matched=False,
            confidence=round(best_score, 4),
            match_method="none",
        )

    def _best_structured(
        self, rules: list[Rule], decision: Decision
    ) -> tuple[Rule | None, float, str]:
        best_rule: Rule | None = None
        best_score = 0.0
        for rule in rules:
            score = self._predicate_matcher.match(rule, decision)
            if score > best_score:
                best_score = score
                best_rule = rule
        return best_rule, best_score, "structured_predicate"

    def _best_similarity(
        self, rules: list[Rule], decision: Decision
    ) -> tuple[Rule | None, float]:
        best_rule: Rule | None = None
        best_score = 0.0
        for rule in rules:
            score = self._similarity_matcher.match(rule, decision)
            if score > best_score:
                best_score = score
                best_rule = rule
        return best_rule, best_score

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

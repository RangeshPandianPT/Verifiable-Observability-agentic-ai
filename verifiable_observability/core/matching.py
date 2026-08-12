"""
Pluggable matcher implementations for Rule Bank decision matching.

Two matchers are provided:
1. StructuredPredicateMatcher — evaluates ObservationPattern predicates against a
   Decision's observation_metadata.  Fast, deterministic, no ML needed.
2. SimilarityMatcher — TF-IDF cosine similarity fallback for when structured
   predicates alone aren't sufficient.

The RuleBank uses StructuredPredicateMatcher first; if no rule reaches the
confidence threshold it falls back to SimilarityMatcher.

Confidence threshold: 0.6
  - Exact structured-predicate match → confidence 1.0
  - Partial structured match → 0.0 < confidence < 1.0 (fraction of fields matched)
  - Similarity match → cosine similarity score (0.0–1.0)
  - Below 0.6 → treated as no match
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from collections import Counter

from verifiable_observability.storage.models import Decision, ObservationPattern, Rule

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# Abstract matcher interface
# ---------------------------------------------------------------------------


class MatcherBase(ABC):
    """Returns a confidence score in [0.0, 1.0] for a rule/decision pair."""

    @abstractmethod
    def match(self, rule: Rule, decision: Decision) -> float:
        """
        Score how well a rule's observation_pattern matches a decision.

        Args:
            rule:     Candidate rule from the Rule Bank.
            decision: Decision to evaluate.

        Returns:
            Confidence in [0.0, 1.0].  Values below CONFIDENCE_THRESHOLD
            should be treated as "no match" by the caller.
        """
        ...


# ---------------------------------------------------------------------------
# 1. Structured Predicate Matcher
# ---------------------------------------------------------------------------


class StructuredPredicateMatcher(MatcherBase):
    """
    Evaluates an ObservationPattern's structured predicates against a Decision.

    Each predicate is one "vote".  The final score is:
        matched_predicates / total_predicates

    Predicate types:
      domain              — exact enum match
      task_type           — exact string match
      numeric_conditions  — supports gt, gte, lt, lte, eq operators
      reasoning_keywords  — ALL keywords must appear in reasoning text (case-insensitive)
      metadata_conditions — key=value exact matches on decision.observation_metadata
    """

    def match(self, rule: Rule, decision: Decision) -> float:
        pattern = rule.observation_pattern
        predicates: list[bool] = []

        # --- domain ---
        if pattern.domain is not None:
            predicates.append(
                decision.observation_metadata.get("domain") == pattern.domain.value
            )

        # --- task_type ---
        if pattern.task_type is not None:
            predicates.append(
                decision.observation_metadata.get("task_type") == pattern.task_type
            )

        # --- numeric_conditions ---
        for field, ops in pattern.numeric_conditions.items():
            raw = decision.observation_metadata.get(field)
            if raw is None:
                predicates.append(False)
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                predicates.append(False)
                continue
            field_ok = True
            for op, threshold in ops.items():
                if op == "gt":
                    field_ok = field_ok and value > threshold
                elif op == "gte":
                    field_ok = field_ok and value >= threshold
                elif op == "lt":
                    field_ok = field_ok and value < threshold
                elif op == "lte":
                    field_ok = field_ok and value <= threshold
                elif op == "eq":
                    field_ok = field_ok and math.isclose(value, threshold)
                else:
                    logger.warning("Unknown numeric operator: %s", op)
                    field_ok = False
            predicates.append(field_ok)

        # --- reasoning_keywords ---
        if pattern.reasoning_keywords:
            reasoning_lower = decision.reasoning.lower()
            all_present = all(
                kw.lower() in reasoning_lower for kw in pattern.reasoning_keywords
            )
            predicates.append(all_present)

        # --- metadata_conditions ---
        for key, expected in pattern.metadata_conditions.items():
            predicates.append(
                decision.observation_metadata.get(key) == expected
            )

        if not predicates:
            # Empty pattern — matches everything with low confidence
            return 0.5

        score = sum(predicates) / len(predicates)
        return score


# ---------------------------------------------------------------------------
# 2. Similarity Matcher (TF-IDF cosine)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Simple whitespace+punctuation tokenizer."""
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _tf(tokens: list[str]) -> Counter:
    return Counter(tokens)


def _cosine(a: Counter, b: Counter) -> float:
    """Cosine similarity between two term-frequency counters."""
    dot = sum(a[t] * b[t] for t in a if t in b)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SimilarityMatcher(MatcherBase):
    """
    TF-IDF cosine similarity between a rule's textual description
    (name + description + keywords) and the decision's reasoning text.

    Used as a fallback when structured predicates alone can't find a match.
    """

    def match(self, rule: Rule, decision: Decision) -> float:
        # Build the rule's "document" from all its text fields
        rule_text = " ".join(
            filter(
                None,
                [
                    rule.name,
                    rule.description,
                    rule.observation_pattern.task_type or "",
                    " ".join(rule.observation_pattern.reasoning_keywords),
                    rule.prescribed_action_pattern.description,
                ],
            )
        )
        rule_tokens = _tokenize(rule_text)
        decision_tokens = _tokenize(decision.reasoning)

        if not rule_tokens or not decision_tokens:
            return 0.0

        return _cosine(_tf(rule_tokens), _tf(decision_tokens))

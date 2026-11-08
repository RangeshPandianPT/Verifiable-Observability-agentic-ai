"""
Pluggable matcher implementations for Rule Bank decision matching.

Architecture & Components:
1. MatchDiagnostics — structured audit trail explaining exactly which predicates
   passed, failed, actual values, and confidence derivation.
2. StructuredPredicateMatcher — evaluates ObservationPattern predicates against a
   Decision's observation_metadata. Deterministic, fast, symbolic. Supports
   rich numeric operators (gt, gte, lt, lte, eq, ne, between, in, not_in),
   regex/substring/dict metadata operators, and negative/regex keywords.
3. SimilarityMatcher — cosine similarity fallback with English stopword filtering,
   token normalization, and length-penalty damping.
4. HybridMatcher — blends deterministic structured checks with semantic similarity
   for robust multi-turn reasoning verification.

Confidence threshold: 0.6
  - Exact structured-predicate match -> confidence 1.0
  - Partial structured match -> 0.0 < confidence < 1.0 (fraction of fields matched)
  - Similarity match -> normalized cosine similarity score (0.0-1.0)
  - Below 0.6 -> treated as no match
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from verifiable_observability.storage.models import Decision, ObservationPattern, Rule

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6

# Standard English stopwords to filter out grammatical noise during text similarity
STOPWORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves",
})


# ---------------------------------------------------------------------------
# Diagnostics & Audit Trail
# ---------------------------------------------------------------------------


@dataclass
class MatchDiagnostics:
    """
    Transparent, auditable breakdown of a rule/decision matching evaluation.
    Provides complete visibility into why a rule triggered or failed to trigger.
    """

    rule_id: str
    score: float
    matched: bool
    method: str
    passed_predicates: list[str] = field(default_factory=list)
    failed_predicates: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "score": round(self.score, 4),
            "matched": self.matched,
            "method": self.method,
            "passed_predicates": self.passed_predicates,
            "failed_predicates": self.failed_predicates,
            "details": self.details,
            "summary": self.summary,
        }


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
            Confidence in [0.0, 1.0]. Values below CONFIDENCE_THRESHOLD
            should be treated as "no match" by the caller.
        """
        ...

    def match_explain(self, rule: Rule, decision: Decision) -> MatchDiagnostics:
        """
        Explain the matching evaluation with full predicate diagnostic details.
        Default implementation delegates to match(). Subclasses should override.
        """
        score = self.match(rule, decision)
        matched = score >= CONFIDENCE_THRESHOLD
        return MatchDiagnostics(
            rule_id=rule.rule_id,
            score=score,
            matched=matched,
            method=self.__class__.__name__,
            summary=f"Score: {score:.2f} ({'MATCH' if matched else 'NO_MATCH'})",
        )


# ---------------------------------------------------------------------------
# 1. Structured Predicate Matcher
# ---------------------------------------------------------------------------


class StructuredPredicateMatcher(MatcherBase):
    """
    Evaluates an ObservationPattern's structured predicates against a Decision.

    Each predicate is one vote. The final score is:
        matched_predicates / total_predicates

    Supported predicate types:
      - domain: exact enum match against observation_metadata['domain']
      - task_type: exact string match against observation_metadata['task_type']
      - numeric_conditions:
          * gt, gte, lt, lte, eq, ne
          * between: [min, max] (inclusive range)
          * in: list of allowed numeric values
          * not_in: list of disallowed numeric values
      - reasoning_keywords:
          * standard keyword: case-insensitive substring
          * negative keyword: prefix '!' or '-' (must NOT be present in reasoning)
          * regex keyword: prefix 'regex:'
      - metadata_conditions:
          * exact equality
          * 'regex:<pattern>': regex search on string representation
          * 'contains:<text>': case-insensitive substring
          * 'prefix:<text>' / 'suffix:<text>'
          * list/set: value is in expected list
          * dict operators: {'ne': ..., 'in': [...], 'not_in': [...]}
    """

    def match(self, rule: Rule, decision: Decision) -> float:
        diag = self.match_explain(rule, decision)
        return diag.score

    def match_explain(self, rule: Rule, decision: Decision) -> MatchDiagnostics:
        pattern = rule.observation_pattern
        passed: list[str] = []
        failed: list[str] = []
        details: dict[str, Any] = {}

        # --- 1. domain ---
        if pattern.domain is not None:
            actual_domain = decision.observation_metadata.get("domain")
            expected_domain = pattern.domain.value
            if actual_domain == expected_domain:
                passed.append(f"domain == {expected_domain}")
            else:
                failed.append(f"domain == {expected_domain} (actual: {actual_domain})")

        # --- 2. task_type ---
        if pattern.task_type is not None:
            actual_task = decision.observation_metadata.get("task_type")
            expected_task = pattern.task_type
            if actual_task == expected_task:
                passed.append(f"task_type == '{expected_task}'")
            else:
                failed.append(f"task_type == '{expected_task}' (actual: '{actual_task}')")

        # --- 3. numeric_conditions ---
        for field_name, ops in pattern.numeric_conditions.items():
            raw_val = decision.observation_metadata.get(field_name)
            if raw_val is None:
                failed.append(f"numeric '{field_name}' present (missing)")
                continue

            try:
                num_val = float(raw_val)
            except (TypeError, ValueError):
                failed.append(f"numeric '{field_name}' valid float (got {raw_val!r})")
                continue

            for op, threshold in ops.items():
                pred_label = f"numeric {field_name} {op} {threshold}"
                field_ok = False
                try:
                    if op == "gt":
                        field_ok = num_val > float(threshold)
                    elif op == "gte":
                        field_ok = num_val >= float(threshold)
                    elif op == "lt":
                        field_ok = num_val < float(threshold)
                    elif op == "lte":
                        field_ok = num_val <= float(threshold)
                    elif op == "eq":
                        field_ok = math.isclose(num_val, float(threshold))
                    elif op == "ne":
                        field_ok = not math.isclose(num_val, float(threshold))
                    elif op == "between":
                        if isinstance(threshold, (list, tuple)) and len(threshold) == 2:
                            field_ok = float(threshold[0]) <= num_val <= float(threshold[1])
                        else:
                            field_ok = False
                    elif op == "in":
                        if isinstance(threshold, (list, tuple, set)):
                            field_ok = any(math.isclose(num_val, float(t)) for t in threshold)
                        else:
                            field_ok = False
                    elif op == "not_in":
                        if isinstance(threshold, (list, tuple, set)):
                            field_ok = all(not math.isclose(num_val, float(t)) for t in threshold)
                        else:
                            field_ok = False
                    else:
                        logger.warning("Unknown numeric operator: %s", op)
                        field_ok = False
                except (TypeError, ValueError) as err:
                    logger.debug("Evaluation error for %s: %s", pred_label, err)
                    field_ok = False

                if field_ok:
                    passed.append(f"{pred_label} (actual: {num_val})")
                else:
                    failed.append(f"{pred_label} (actual: {num_val})")

        # --- 4. reasoning_keywords ---
        if pattern.reasoning_keywords:
            reasoning_lower = decision.reasoning.lower()
            for kw in pattern.reasoning_keywords:
                kw_str = str(kw).strip()
                if kw_str.startswith("!") or kw_str.startswith("-"):
                    # Negative keyword: must NOT appear
                    forbidden = kw_str[1:].lower().strip()
                    if forbidden and forbidden in reasoning_lower:
                        failed.append(f"reasoning NOT contains '{forbidden}' (found forbidden)")
                    else:
                        passed.append(f"reasoning NOT contains '{forbidden}'")
                elif kw_str.startswith("regex:"):
                    pat = kw_str[6:]
                    if re.search(pat, decision.reasoning, re.IGNORECASE):
                        passed.append(f"reasoning regex '{pat}'")
                    else:
                        failed.append(f"reasoning regex '{pat}' (no match)")
                else:
                    target = kw_str.lower()
                    if target in reasoning_lower:
                        passed.append(f"reasoning contains '{target}'")
                    else:
                        failed.append(f"reasoning contains '{target}' (missing)")

        # --- 5. metadata_conditions ---
        for meta_key, expected in pattern.metadata_conditions.items():
            actual = decision.observation_metadata.get(meta_key)
            pred_desc = f"metadata['{meta_key}']"

            cond_ok = False
            if isinstance(expected, str) and expected.startswith("regex:"):
                pattern_re = expected[6:]
                cond_ok = actual is not None and bool(re.search(pattern_re, str(actual)))
                pred_desc += f" matches regex '{pattern_re}'"
            elif isinstance(expected, str) and expected.startswith("contains:"):
                substr = expected[9:].lower()
                cond_ok = actual is not None and substr in str(actual).lower()
                pred_desc += f" contains '{substr}'"
            elif isinstance(expected, str) and expected.startswith("prefix:"):
                pfx = expected[7:]
                cond_ok = actual is not None and str(actual).startswith(pfx)
                pred_desc += f" starts with '{pfx}'"
            elif isinstance(expected, str) and expected.startswith("suffix:"):
                sfx = expected[7:]
                cond_ok = actual is not None and str(actual).endswith(sfx)
                pred_desc += f" ends with '{sfx}'"
            elif isinstance(expected, dict):
                # Dict conditions: {'in': [...], 'not_in': [...], 'ne': ...}
                dict_ok = True
                if "ne" in expected and actual == expected["ne"]:
                    dict_ok = False
                if "in" in expected and (actual not in expected["in"]):
                    dict_ok = False
                if "not_in" in expected and (actual in expected["not_in"]):
                    dict_ok = False
                cond_ok = dict_ok
                pred_desc += f" satisfies dict condition {expected}"
            elif isinstance(expected, (list, tuple, set)):
                cond_ok = actual in expected
                pred_desc += f" in {expected}"
            else:
                cond_ok = (actual == expected)
                pred_desc += f" == {expected!r}"

            if cond_ok:
                passed.append(f"{pred_desc} (actual: {actual!r})")
            else:
                failed.append(f"{pred_desc} (actual: {actual!r})")

        total_preds = len(passed) + len(failed)
        if total_preds == 0:
            # Empty pattern matches everything with baseline confidence
            score = 0.5
        else:
            score = len(passed) / total_preds

        matched = score >= CONFIDENCE_THRESHOLD
        summary = (
            f"Structured match {score:.2f} ({len(passed)}/{total_preds} passed) - "
            f"{'MATCH' if matched else 'NO_MATCH'}"
        )

        details["passed_count"] = len(passed)
        details["failed_count"] = len(failed)
        details["total_predicates"] = total_preds

        return MatchDiagnostics(
            rule_id=rule.rule_id,
            score=score,
            matched=matched,
            method="StructuredPredicateMatcher",
            passed_predicates=passed,
            failed_predicates=failed,
            details=details,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# 2. Similarity Matcher (TF-IDF cosine with Stopword Filtering)
# ---------------------------------------------------------------------------


def _tokenize(text: str, filter_stopwords: bool = True) -> list[str]:
    """Tokenize into lowercase alphanumeric tokens, filtering stopwords if requested."""
    raw_tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    if not filter_stopwords:
        return raw_tokens

    filtered = [t for t in raw_tokens if t not in STOPWORDS and len(t) > 1]
    # Fallback to raw tokens if all tokens were stopwords (e.g. short queries)
    return filtered if filtered else raw_tokens


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
    Cosine similarity between a rule's textual description (name + description +
    keywords + prescribed action) and the decision's reasoning text.

    Filters English stopwords to eliminate grammatical noise and prevent
    false-positive semantic matches.
    """

    def __init__(self, filter_stopwords: bool = True, length_penalty: bool = False) -> None:
        self.filter_stopwords = filter_stopwords
        self.length_penalty = length_penalty

    def match(self, rule: Rule, decision: Decision) -> float:
        diag = self.match_explain(rule, decision)
        return diag.score

    def match_explain(self, rule: Rule, decision: Decision) -> MatchDiagnostics:
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
        rule_tokens = _tokenize(rule_text, filter_stopwords=self.filter_stopwords)
        decision_tokens = _tokenize(decision.reasoning, filter_stopwords=self.filter_stopwords)

        if not rule_tokens or not decision_tokens:
            return MatchDiagnostics(
                rule_id=rule.rule_id,
                score=0.0,
                matched=False,
                method="SimilarityMatcher",
                failed_predicates=["No valid tokens found for comparison"],
                summary="Empty token set -> similarity 0.0",
            )

        sim = _cosine(_tf(rule_tokens), _tf(decision_tokens))

        # Optional gentle length penalty if lengths differ astronomically
        if self.length_penalty and sim > 0:
            ratio = min(len(rule_tokens), len(decision_tokens)) / max(len(rule_tokens), len(decision_tokens))
            sim = sim * (0.8 + 0.2 * ratio)

        matched = sim >= CONFIDENCE_THRESHOLD
        overlap_tokens = sorted(list(set(rule_tokens) & set(decision_tokens)))

        details = {
            "rule_tokens_count": len(rule_tokens),
            "decision_tokens_count": len(decision_tokens),
            "overlap_token_count": len(overlap_tokens),
            "overlap_samples": overlap_tokens[:10],
        }

        passed = [f"Token overlap: {len(overlap_tokens)} shared tokens"] if overlap_tokens else []
        failed = ["Low token overlap"] if not matched else []

        return MatchDiagnostics(
            rule_id=rule.rule_id,
            score=sim,
            matched=matched,
            method="SimilarityMatcher",
            passed_predicates=passed,
            failed_predicates=failed,
            details=details,
            summary=f"Cosine similarity: {sim:.2f} ({'MATCH' if matched else 'NO_MATCH'})",
        )


# ---------------------------------------------------------------------------
# 3. Hybrid Matcher (Structured + Semantic Blend)
# ---------------------------------------------------------------------------


class HybridMatcher(MatcherBase):
    """
    Blends deterministic StructuredPredicateMatcher with SimilarityMatcher.

    Strategy:
      - If structured predicate match is 1.0 (exact), return 1.0 immediately.
      - If structured match score is below min_predicate_score, reject immediately
        (prevents high semantic similarity from bypassing hard domain/parameter constraints).
      - Otherwise, score = predicate_weight * pred_score + similarity_weight * sim_score.
    """

    def __init__(
        self,
        predicate_weight: float = 0.7,
        similarity_weight: float = 0.3,
        min_predicate_score: float = 0.4,
    ) -> None:
        self.predicate_weight = predicate_weight
        self.similarity_weight = similarity_weight
        self.min_predicate_score = min_predicate_score
        self.predicate_matcher = StructuredPredicateMatcher()
        self.similarity_matcher = SimilarityMatcher()

    def match(self, rule: Rule, decision: Decision) -> float:
        diag = self.match_explain(rule, decision)
        return diag.score

    def match_explain(self, rule: Rule, decision: Decision) -> MatchDiagnostics:
        p_diag = self.predicate_matcher.match_explain(rule, decision)

        # Exact structured match short-circuits
        if math.isclose(p_diag.score, 1.0):
            return MatchDiagnostics(
                rule_id=rule.rule_id,
                score=1.0,
                matched=True,
                method="HybridMatcher (Decisive Structured)",
                passed_predicates=p_diag.passed_predicates,
                failed_predicates=p_diag.failed_predicates,
                details={"predicate_diagnostics": p_diag.to_dict()},
                summary="Exact structured match -> 1.0",
            )

        # Hard barrier: if structured predicates scored below minimum, do not boost with similarity
        if p_diag.score < self.min_predicate_score:
            return MatchDiagnostics(
                rule_id=rule.rule_id,
                score=p_diag.score,
                matched=False,
                method="HybridMatcher (Rejected by Structured Floor)",
                passed_predicates=p_diag.passed_predicates,
                failed_predicates=p_diag.failed_predicates + [
                    f"Structured score {p_diag.score:.2f} < floor {self.min_predicate_score:.2f}"
                ],
                details={"predicate_diagnostics": p_diag.to_dict()},
                summary=f"Structured score {p_diag.score:.2f} below floor {self.min_predicate_score:.2f}",
            )

        # Blend
        s_diag = self.similarity_matcher.match_explain(rule, decision)
        blended_score = (self.predicate_weight * p_diag.score) + (self.similarity_weight * s_diag.score)
        matched = blended_score >= CONFIDENCE_THRESHOLD

        return MatchDiagnostics(
            rule_id=rule.rule_id,
            score=blended_score,
            matched=matched,
            method="HybridMatcher",
            passed_predicates=p_diag.passed_predicates + s_diag.passed_predicates,
            failed_predicates=p_diag.failed_predicates + s_diag.failed_predicates,
            details={
                "predicate_score": p_diag.score,
                "similarity_score": s_diag.score,
                "weights": {
                    "predicate": self.predicate_weight,
                    "similarity": self.similarity_weight,
                },
            },
            summary=(
                f"Hybrid score {blended_score:.2f} (Pred: {p_diag.score:.2f}, "
                f"Sim: {s_diag.score:.2f}) - {'MATCH' if matched else 'NO_MATCH'}"
            ),
        )

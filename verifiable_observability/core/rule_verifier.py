"""
Formal Rule Verifier — Phase 6/7.

Analyzes declarative rules stored in the Rule Bank for:
  1. Contradictions & Conflicts: Rules matching overlapping observation
     patterns that prescribe conflicting actions or constraints.
  2. Subsumption & Shadowing: Rules whose conditions are completely subsumed
     by broader rules, rendering the narrower rule redundant or unreachable.
  3. Dead / Unsatisfiable Rules: Rules containing mutually exclusive predicates
     (e.g., amount > 50000 AND amount <= 10000) that can never trigger.
  4. Coverage Gaps: High-risk domain tools or task types that lack vetted,
     verified rule coverage in the Rule Bank.

Theoretical grounding:
  - ICSE 2026 AgentSpec (trigger-predicate-enforcement consistency)
  - ICLR 2026 VeriGuard (policy contract verification)
  - Winston et al. 2026 (solver-aided verification of policy compliance)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from verifiable_observability.storage.models import (
    ActionPattern,
    Domain,
    ObservationPattern,
    Rule,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conflict & Report Data Structures
# ---------------------------------------------------------------------------


class ConflictSeverity(str, Enum):
    CRITICAL = "critical"  # Direct contradiction (e.g. required vs forbidden)
    HIGH = "high"          # Parameter mismatch or shadowed action
    MEDIUM = "medium"      # Redundant subsumption
    LOW = "low"            # Overlapping keyword heuristics


class ConflictType(str, Enum):
    DIRECT_CONTRADICTION = "direct_contradiction"
    PARAMETER_CONFLICT = "parameter_conflict"
    SHADOWED_RULE = "shadowed_rule"
    UNSATISFIABLE_PREDICATE = "unsatisfiable_predicate"
    MUTUALLY_EXCLUSIVE_REQUIREMENTS = "mutually_exclusive_requirements"


@dataclass
class RuleConflict:
    """Represents an identified conflict between two rules or within one rule."""

    conflict_type: ConflictType
    severity: ConflictSeverity
    rule_id_a: str
    rule_id_b: str | None
    domain: Domain
    description: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CoverageGap:
    """Represents a sensitive operation or domain area lacking verified rule coverage."""

    domain: Domain
    target: str  # tool name or task type
    severity: ConflictSeverity
    description: str
    recommended_action: str


@dataclass
class RuleVerificationReport:
    """Full formal verification report for a rule set."""

    domain: Domain | None
    total_rules_inspected: int
    is_sound: bool
    conflicts: list[RuleConflict] = field(default_factory=list)
    dead_rules: list[str] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# Known critical tools per domain that must have rule coverage
CRITICAL_DOMAIN_TOOLS: dict[Domain, list[str]] = {
    Domain.FINANCE: ["execute_transfer", "execute_trade", "rebalance_portfolio"],
    Domain.HEALTHCARE: ["prescribe_medication", "update_patient_record", "access_phi"],
    Domain.CODE_EXECUTION: ["execute_command", "modify_system_file", "spawn_process"],
    Domain.ECOMMERCE: ["process_order", "issue_refund", "update_inventory"],
}


# ---------------------------------------------------------------------------
# Formal Rule Verifier Implementation
# ---------------------------------------------------------------------------


class RuleVerifier:
    """
    Formal static analysis engine for the Rule Bank.

    Evaluates rule sets for logical consistency, contradiction freedom,
    subsumption redundancy, and safety coverage.
    """

    def __init__(self, domain: Domain | None = None) -> None:
        self.domain = domain

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def verify_rule_set(
        self,
        rules: list[Rule],
        domain: Domain | None = None,
        check_coverage: bool = True,
    ) -> RuleVerificationReport:
        """
        Verify an entire set of rules for consistency, contradictions, and coverage.

        Args:
            rules: List of Rule objects to inspect.
            domain: Optional domain filter.
            check_coverage: If True, identifies critical tools lacking coverage.

        Returns:
            A RuleVerificationReport containing all findings.
        """
        target_domain = domain or self.domain
        filtered_rules = [
            r for r in rules
            if (target_domain is None or r.domain == target_domain)
            and r.verification_status != VerificationStatus.DEPRECATED
        ]

        conflicts: list[RuleConflict] = []
        dead_rules: list[str] = []

        # 1. Single-rule satisfiability checks (dead rules)
        for rule in filtered_rules:
            dead_conflict = self._check_rule_satisfiability(rule)
            if dead_conflict:
                conflicts.append(dead_conflict)
                dead_rules.append(rule.rule_id)

        # 2. Pairwise contradiction and subsumption checks
        n = len(filtered_rules)
        for i in range(n):
            for j in range(i + 1, n):
                pair_conflicts = self._check_rule_pair(
                    filtered_rules[i], filtered_rules[j]
                )
                conflicts.extend(pair_conflicts)

        # 3. Coverage gap analysis
        coverage_gaps: list[CoverageGap] = []
        if check_coverage and target_domain in CRITICAL_DOMAIN_TOOLS:
            coverage_gaps = self.find_coverage_gaps(filtered_rules, target_domain)

        is_sound = (
            len([c for c in conflicts if c.severity == ConflictSeverity.CRITICAL]) == 0
            and len(dead_rules) == 0
        )

        critical_count = sum(1 for c in conflicts if c.severity == ConflictSeverity.CRITICAL)
        high_count = sum(1 for c in conflicts if c.severity == ConflictSeverity.HIGH)
        med_count = sum(1 for c in conflicts if c.severity == ConflictSeverity.MEDIUM)

        report = RuleVerificationReport(
            domain=target_domain,
            total_rules_inspected=len(filtered_rules),
            is_sound=is_sound,
            conflicts=conflicts,
            dead_rules=dead_rules,
            coverage_gaps=coverage_gaps,
            summary={
                "critical_conflicts": critical_count,
                "high_conflicts": high_count,
                "medium_conflicts": med_count,
                "dead_rules_count": len(dead_rules),
                "coverage_gaps_count": len(coverage_gaps),
            },
        )
        return report

    def check_new_rule(
        self,
        existing_rules: list[Rule],
        new_rule: Rule,
    ) -> list[RuleConflict]:
        """
        Check if adding a new rule introduces contradictions or dead states.

        Useful at rule ingestion time before promoting to VERIFIED.
        """
        conflicts: list[RuleConflict] = []

        # Check self-satisfiability
        dead = self._check_rule_satisfiability(new_rule)
        if dead:
            conflicts.append(dead)

        # Check against existing verified rules
        for existing in existing_rules:
            if existing.verification_status == VerificationStatus.DEPRECATED:
                continue
            if existing.domain != new_rule.domain:
                continue
            pair_conflicts = self._check_rule_pair(existing, new_rule)
            conflicts.extend(pair_conflicts)

        return conflicts

    def find_coverage_gaps(
        self,
        rules: list[Rule],
        domain: Domain,
    ) -> list[CoverageGap]:
        """Check whether all critical tools in a domain have active rule coverage."""
        gaps: list[CoverageGap] = []
        critical_tools = CRITICAL_DOMAIN_TOOLS.get(domain, [])

        covered_tools: set[str] = set()
        for r in rules:
            if r.domain == domain and r.verification_status != VerificationStatus.DEPRECATED:
                tool = r.prescribed_action_pattern.tool_name
                if tool:
                    covered_tools.add(tool)

        for expected_tool in critical_tools:
            if expected_tool not in covered_tools:
                gaps.append(
                    CoverageGap(
                        domain=domain,
                        target=expected_tool,
                        severity=ConflictSeverity.HIGH,
                        description=(
                            f"Critical tool '{expected_tool}' in domain '{domain.value}' "
                            "has no verified rules in the Rule Bank."
                        ),
                        recommended_action=(
                            f"Define at least one verified Rule specifying valid triggers "
                            f"and parameter constraints for tool '{expected_tool}'."
                        ),
                    )
                )

        return gaps

    # -----------------------------------------------------------------------
    # Internal Formal Checks
    # -----------------------------------------------------------------------

    def _check_rule_satisfiability(self, rule: Rule) -> RuleConflict | None:
        """
        Check if a rule's internal conditions are logically satisfiable.

        e.g., checks if a numeric interval has min > max.
        """
        obs = rule.observation_pattern

        # Check numeric conditions: {var: {"gt": X, "lte": Y}}
        for var_name, bounds in obs.numeric_conditions.items():
            lower = bounds.get("gt", bounds.get("gte", float("-inf")))
            upper = bounds.get("lt", bounds.get("lte", float("inf")))

            if lower >= upper:
                return RuleConflict(
                    conflict_type=ConflictType.UNSATISFIABLE_PREDICATE,
                    severity=ConflictSeverity.CRITICAL,
                    rule_id_a=rule.rule_id,
                    rule_id_b=None,
                    domain=rule.domain,
                    description=(
                        f"Rule '{rule.name}' ({rule.rule_id[:8]}) has unsatisfiable numeric "
                        f"predicate for '{var_name}': lower bound ({lower}) >= upper bound ({upper})."
                    ),
                    details={"variable": var_name, "lower": lower, "upper": upper},
                )

        # Check action pattern parameter contradictions
        act = rule.prescribed_action_pattern
        required_keys = set(act.required_parameters.keys())
        forbidden_keys = set(act.forbidden_parameters)
        overlap = required_keys & forbidden_keys
        if overlap:
            return RuleConflict(
                conflict_type=ConflictType.MUTUALLY_EXCLUSIVE_REQUIREMENTS,
                severity=ConflictSeverity.CRITICAL,
                rule_id_a=rule.rule_id,
                rule_id_b=None,
                domain=rule.domain,
                description=(
                    f"Rule '{rule.name}' ({rule.rule_id[:8]}) has conflicting parameter requirements: "
                    f"parameters {sorted(overlap)} are marked as both REQUIRED and FORBIDDEN."
                ),
                details={"conflicting_parameters": list(overlap)},
            )

        return None

    def _check_rule_pair(self, r1: Rule, r2: Rule) -> list[RuleConflict]:
        """Inspect two rules for mutual contradictions, parameter clashes, or shadowing."""
        conflicts: list[RuleConflict] = []

        # Only rules within the same domain can conflict
        if r1.domain != r2.domain:
            return conflicts

        obs1 = r1.observation_pattern
        obs2 = r2.observation_pattern

        # Check if task_types are compatible
        if obs1.task_type and obs2.task_type and obs1.task_type != obs2.task_type:
            # Different explicit task types; they don't fire on the same context
            return conflicts

        # Check numeric predicate intersection
        if not self._numeric_conditions_intersect(obs1.numeric_conditions, obs2.numeric_conditions):
            # The observation patterns never co-occur
            return conflicts

        # Observation patterns can co-occur! Now evaluate actions.
        act1 = r1.prescribed_action_pattern
        act2 = r2.prescribed_action_pattern

        # Conflict 1: One rule forbids a parameter that the other rule requires
        req1 = set(act1.required_parameters.keys())
        forb1 = set(act1.forbidden_parameters)
        req2 = set(act2.required_parameters.keys())
        forb2 = set(act2.forbidden_parameters)

        clash1 = req1 & forb2
        clash2 = req2 & forb1
        clash = clash1 | clash2
        if clash:
            conflicts.append(
                RuleConflict(
                    conflict_type=ConflictType.DIRECT_CONTRADICTION,
                    severity=ConflictSeverity.CRITICAL,
                    rule_id_a=r1.rule_id,
                    rule_id_b=r2.rule_id,
                    domain=r1.domain,
                    description=(
                        f"Direct contradiction between '{r1.name}' ({r1.rule_id[:8]}) and "
                        f"'{r2.name}' ({r2.rule_id[:8]}): parameter(s) {sorted(clash)} are "
                        "required by one rule but forbidden by the other."
                    ),
                    details={"clashing_parameters": list(clash)},
                )
            )

        # Conflict 2: Both rules specify the same tool with different required parameter values
        if act1.tool_name and act2.tool_name and act1.tool_name == act2.tool_name:
            for param, val1 in act1.required_parameters.items():
                if param in act2.required_parameters:
                    val2 = act2.required_parameters[param]
                    if val1 != val2:
                        conflicts.append(
                            RuleConflict(
                                conflict_type=ConflictType.PARAMETER_CONFLICT,
                                severity=ConflictSeverity.CRITICAL,
                                rule_id_a=r1.rule_id,
                                rule_id_b=r2.rule_id,
                                domain=r1.domain,
                                description=(
                                    f"Conflicting required parameter values for tool '{act1.tool_name}': "
                                    f"'{r1.name}' requires {param}={val1}, while '{r2.name}' requires {param}={val2}."
                                ),
                                details={"parameter": param, "val1": val1, "val2": val2},
                            )
                        )

        # Check for Subsumption / Shadowing
        if self._is_subsumed(r1, r2):
            conflicts.append(
                RuleConflict(
                    conflict_type=ConflictType.SHADOWED_RULE,
                    severity=ConflictSeverity.MEDIUM,
                    rule_id_a=r1.rule_id,
                    rule_id_b=r2.rule_id,
                    domain=r1.domain,
                    description=(
                        f"Rule '{r1.name}' ({r1.rule_id[:8]}) is completely subsumed by broader rule "
                        f"'{r2.name}' ({r2.rule_id[:8]}) with identical action prescriptions."
                    ),
                    details={"subsumed_rule": r1.rule_id, "broader_rule": r2.rule_id},
                )
            )
        elif self._is_subsumed(r2, r1):
            conflicts.append(
                RuleConflict(
                    conflict_type=ConflictType.SHADOWED_RULE,
                    severity=ConflictSeverity.MEDIUM,
                    rule_id_a=r2.rule_id,
                    rule_id_b=r1.rule_id,
                    domain=r2.domain,
                    description=(
                        f"Rule '{r2.name}' ({r2.rule_id[:8]}) is completely subsumed by broader rule "
                        f"'{r1.name}' ({r1.rule_id[:8]}) with identical action prescriptions."
                    ),
                    details={"subsumed_rule": r2.rule_id, "broader_rule": r1.rule_id},
                )
            )

        return conflicts

    def _numeric_conditions_intersect(
        self,
        conds1: dict[str, dict[str, float]],
        conds2: dict[str, dict[str, float]],
    ) -> bool:
        """
        Check if two sets of numeric conditions share a non-empty intersection.

        Returns True if they overlap or if either condition set is unconstrained.
        """
        shared_vars = set(conds1.keys()) & set(conds2.keys())
        for var in shared_vars:
            b1 = conds1[var]
            b2 = conds2[var]

            low1 = b1.get("gt", b1.get("gte", float("-inf")))
            high1 = b1.get("lt", b1.get("lte", float("inf")))

            low2 = b2.get("gt", b2.get("gte", float("-inf")))
            high2 = b2.get("lt", b2.get("lte", float("inf")))

            # Two intervals [L1, H1] and [L2, H2] intersect iff max(L1, L2) <= min(H1, H2)
            if max(low1, low2) > min(high1, high2):
                return False  # Disjoint intervals

        return True

    def _is_subsumed(self, narrow: Rule, broad: Rule) -> bool:
        """
        Returns True if 'narrow' is strictly contained within 'broad' with the same action.
        """
        # Actions must be effectively identical
        act_n = narrow.prescribed_action_pattern
        act_b = broad.prescribed_action_pattern
        if act_n.tool_name != act_b.tool_name:
            return False
        if act_n.required_parameters != act_b.required_parameters:
            return False
        if set(act_n.forbidden_parameters) != set(act_b.forbidden_parameters):
            return False

        obs_n = narrow.observation_pattern
        obs_b = broad.observation_pattern

        # If broad specifies a task_type, narrow must have that same task_type
        if obs_b.task_type and obs_b.task_type != obs_n.task_type:
            return False

        # Numeric bounds: broad must encompass narrow's bounds
        for var, b_bounds in obs_b.numeric_conditions.items():
            if var not in obs_n.numeric_conditions:
                return False
            n_bounds = obs_n.numeric_conditions[var]

            b_low = b_bounds.get("gt", b_bounds.get("gte", float("-inf")))
            b_high = b_bounds.get("lt", b_bounds.get("lte", float("inf")))

            n_low = n_bounds.get("gt", n_bounds.get("gte", float("-inf")))
            n_high = n_bounds.get("lt", n_bounds.get("lte", float("inf")))

            if not (b_low <= n_low and b_high >= n_high):
                return False

        return True

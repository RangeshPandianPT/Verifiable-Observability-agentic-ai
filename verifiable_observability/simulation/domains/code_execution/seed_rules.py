"""
Code Execution Domain — Seed Rule Set (Phase 6)

12 rules covering three Code Execution task types:
  - code_generation
  - code_review
  - system_command_execution

Each rule is an observation→action mapping derived from plausible
"if observation X then approved action Y" pairs a code-execution agent would follow.

All rules are loaded with verification_status=PENDING by default;
call RuleBank.verify_rule(rule_id, verifier) to promote them to VERIFIED.

Usage::

    from verifiable_observability.simulation.domains.code_execution.seed_rules import (
        get_seed_rules,
        load_seed_rules_into_bank,
    )
    rules = get_seed_rules()
    load_seed_rules_into_bank(rule_bank, auto_verify=True)
"""

from __future__ import annotations

from verifiable_observability.core.rule_bank import RuleBankBase
from verifiable_observability.storage.models import (
    ActionPattern,
    Domain,
    ObservationPattern,
    Rule,
)


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------


def get_seed_rules() -> list[Rule]:
    """Return the 12 Code Execution seed rules (unsaved, status=PENDING)."""
    return [

        # ========================
        # CODE GENERATION (4 rules)
        # ========================

        Rule(
            rule_id="ce-cg-001",
            domain=Domain.CODE_EXECUTION,
            name="code_gen_static_analysis_before_run",
            description=(
                "Any generated code must pass static analysis (linting / type-checking) "
                "before it is submitted to a live execution environment."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_generation",
                reasoning_keywords=["generate", "code", "function", "script"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="run_static_analysis",
                description="Run linter and type-checker on generated code before execution.",
                required_parameters={"check_types": True},
            ),
        ),

        Rule(
            rule_id="ce-cg-002",
            domain=Domain.CODE_EXECUTION,
            name="code_gen_sandbox_execution",
            description=(
                "Generated code must be executed inside an isolated sandbox (container or VM); "
                "direct host-system execution is forbidden."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_generation",
                reasoning_keywords=["execute", "run", "sandbox", "container"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="execute_in_sandbox",
                description="Run code in an isolated environment, not on the host.",
                required_parameters={"isolation_level": "container"},
            ),
        ),

        Rule(
            rule_id="ce-cg-003",
            domain=Domain.CODE_EXECUTION,
            name="code_gen_no_network_in_sandbox",
            description=(
                "Sandbox executions must have network access disabled unless the task "
                "explicitly requires it and network access has been pre-approved."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_generation",
                reasoning_keywords=["network", "internet", "request", "http"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_network_policy",
                description="Verify network access policy before launching sandbox.",
                required_parameters={"default_policy": "deny"},
            ),
        ),

        Rule(
            rule_id="ce-cg-004",
            domain=Domain.CODE_EXECUTION,
            name="code_gen_log_generated_artifact",
            description=(
                "Every generated code artifact (function, module, script) must be logged "
                "with its hash, intent, and execution context for auditability."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_generation",
                reasoning_keywords=["artifact", "generate", "log", "hash"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="log_code_artifact",
                description="Record code artifact hash, intent, and context in the audit log.",
            ),
        ),

        # =====================
        # CODE REVIEW (4 rules)
        # =====================

        Rule(
            rule_id="ce-cr-001",
            domain=Domain.CODE_EXECUTION,
            name="code_review_security_scan",
            description=(
                "Before approving any code change, a security vulnerability scan "
                "(SAST) must be run and results reviewed."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_review",
                reasoning_keywords=["review", "security", "vulnerability", "scan"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="run_security_scan",
                description="Run SAST security scan on the code under review.",
                required_parameters={"scan_type": "sast"},
            ),
        ),

        Rule(
            rule_id="ce-cr-002",
            domain=Domain.CODE_EXECUTION,
            name="code_review_dependency_check",
            description=(
                "All third-party dependencies must be checked for known CVEs "
                "using a software composition analysis (SCA) tool before approval."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_review",
                reasoning_keywords=["dependency", "package", "library", "cve"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="run_dependency_check",
                description="SCA scan on all third-party dependencies for known CVEs.",
                required_parameters={"fail_on_severity": "high"},
            ),
        ),

        Rule(
            rule_id="ce-cr-003",
            domain=Domain.CODE_EXECUTION,
            name="code_review_require_human_approval_for_prod",
            description=(
                "Code changes targeting the production branch must receive at least "
                "one human reviewer approval before merge is permitted."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_review",
                reasoning_keywords=["production", "main", "merge", "approve"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="request_human_code_review",
                description="Request human reviewer approval for production-branch changes.",
                required_parameters={"min_approvals": 1},
            ),
        ),

        Rule(
            rule_id="ce-cr-004",
            domain=Domain.CODE_EXECUTION,
            name="code_review_test_coverage_threshold",
            description=(
                "Changes to core modules must not reduce test coverage below the "
                "project threshold (default 80%). Coverage must be verified before approval."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="code_review",
                reasoning_keywords=["test", "coverage", "threshold", "core"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_test_coverage",
                description="Verify code coverage meets or exceeds the project threshold.",
                required_parameters={"min_coverage_pct": 80},
            ),
        ),

        # =================================
        # SYSTEM COMMAND EXECUTION (4 rules)
        # =================================

        Rule(
            rule_id="ce-sc-001",
            domain=Domain.CODE_EXECUTION,
            name="system_cmd_allowlist_check",
            description=(
                "Only commands on the approved allowlist may be dispatched. "
                "Any command not on the list must be blocked and escalated."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="system_command_execution",
                reasoning_keywords=["command", "execute", "shell", "run"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="check_command_allowlist",
                description="Verify the command is on the approved allowlist before execution.",
            ),
        ),

        Rule(
            rule_id="ce-sc-002",
            domain=Domain.CODE_EXECUTION,
            name="system_cmd_no_root_without_approval",
            description=(
                "System commands requiring root/sudo privileges must not be executed "
                "without explicit prior approval from a human operator."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="system_command_execution",
                reasoning_keywords=["root", "sudo", "privilege", "admin"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="request_privilege_escalation_approval",
                description="Submit an approval request before running privileged commands.",
                required_parameters={"privilege_level": "root"},
            ),
        ),

        Rule(
            rule_id="ce-sc-003",
            domain=Domain.CODE_EXECUTION,
            name="system_cmd_timeout_enforced",
            description=(
                "All system command executions must enforce a timeout (default 60 s). "
                "Commands exceeding the timeout must be killed and logged."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="system_command_execution",
                reasoning_keywords=["timeout", "kill", "long-running", "time"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="execute_with_timeout",
                description="Run command with mandatory timeout enforcement.",
                required_parameters={"timeout_seconds": 60},
            ),
        ),

        Rule(
            rule_id="ce-sc-004",
            domain=Domain.CODE_EXECUTION,
            name="system_cmd_audit_trail",
            description=(
                "Every system command execution (attempt, success, or failure) must "
                "be written to the immutable audit trail with user context and timestamp."
            ),
            observation_pattern=ObservationPattern(
                domain=Domain.CODE_EXECUTION,
                task_type="system_command_execution",
                reasoning_keywords=["audit", "log", "trail", "executed", "command"],
            ),
            prescribed_action_pattern=ActionPattern(
                tool_name="write_command_audit_trail",
                description="Write an immutable audit entry for this command execution.",
                required_parameters={"include_user_context": True},
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Loader helper
# ---------------------------------------------------------------------------


def load_seed_rules_into_bank(
    rule_bank: RuleBankBase,
    auto_verify: bool = False,
    verifier: str = "automatic:seed_loader",
) -> list[Rule]:
    """
    Load all Code Execution seed rules into the given Rule Bank.

    Args:
        rule_bank:    The RuleBank instance to load rules into.
        auto_verify:  If True, immediately verify all loaded rules.
        verifier:     Verifier string used when auto_verify=True.

    Returns:
        List of loaded (and possibly verified) Rule objects.
    """
    rules = get_seed_rules()
    loaded = []
    for rule in rules:
        stored = rule_bank.add_rule(rule, provenance="code_execution_seed_v1")
        if auto_verify:
            stored = rule_bank.verify_rule(stored.rule_id, verifier=verifier)
        loaded.append(stored)
    return loaded

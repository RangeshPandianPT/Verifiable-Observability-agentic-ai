"""
Constraint Compliance Monitor (CCM) — enforces safety constraints before action dispatch.

Phase 0: abstract interface + stub (always ALLOW).
Phase 3: FinanceCCM — Finance constraint set.
Phase 6: HealthcareCCM — Healthcare constraint set.
         CodeExecutionCCM — Code Execution constraint set.

Decision outcomes:
    ALLOW  — action is safe to dispatch
    BLOCK  — hard constraint violated; action must NOT be dispatched
    FLAG   — soft constraint violated; action is escalated for review but may proceed
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    ConstraintCheckResult,
    Trajectory,
    ViolatedConstraint,
)


class ConstraintComplianceMonitorBase(ABC):
    """Abstract interface for the Constraint Compliance Monitor."""

    @abstractmethod
    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        """
        Evaluate an action against all active constraints.

        Args:
            action:     The Action the agent intends to dispatch.
            trajectory: Current trajectory (provides context, history).

        Returns:
            ConstraintCheckResult with ALLOW / BLOCK / FLAG decision.
        """
        ...


class StubCCM(ConstraintComplianceMonitorBase):
    """
    Pass-through stub — always returns ALLOW.

    Used in Phase 0 smoke tests. Replaced by the real CCM in Phase 3.
    """

    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=ComplianceDecision.ALLOW,
            violated_constraints=[],
            details="stub: no constraints checked",
        )


# ---------------------------------------------------------------------------
# Finance CCM  (Phase 3 / Phase 6 — no change to existing logic)
# ---------------------------------------------------------------------------

# Hard-blocked tool calls in the Finance domain (regardless of parameters)
_FINANCE_BLOCKED_TOOLS: set[str] = {
    "delete_account",
    "bypass_compliance",
    "override_risk_limit",
    "force_execute",
    "disable_audit_log",
}

# Soft-flagged tool calls in the Finance domain
_FINANCE_FLAGGED_TOOLS: set[str] = {
    "execute_transfer",     # flagged when amount is suspiciously large
    "place_trade_order",    # flagged when quantity is very high
    "request_compliance_approval",  # should be followed by a real transfer
}

_FINANCE_MAX_TRANSFER_USD = 50_000     # hard-block above this without prior compliance approval
_FINANCE_FLAG_TRANSFER_USD = 10_000   # flag above this (soft — needs compliance approval)
_FINANCE_MAX_TRADE_QTY = 100_000      # flag extremely large share quantities


class FinanceCCM(ConstraintComplianceMonitorBase):
    """
    Finance domain Constraint Compliance Monitor (Phase 3+).

    Hard constraints (BLOCK):
      - Attempt to call a tool that is never permitted in Finance.
      - execute_transfer with amount_usd > 500 000 (no prior approval in trajectory).

    Soft constraints (FLAG):
      - execute_transfer with amount_usd > 10 000 (should have compliance approval).
      - place_trade_order with quantity > 100 000 (unusually large order).
    """

    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        violations: list[ViolatedConstraint] = []
        decision = ComplianceDecision.ALLOW

        # --- Hard: blocked tool names ---
        # Input Guardrail Check (Phase 9)
        if action.tool_name == "user_input_prompt":
            prompt = str(action.parameters.get("prompt_text", "")).lower()
            if "ignore all previous instructions" in prompt or "system prompt injection" in prompt:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="fin-hard-input-001",
                        constraint_name="finance_prompt_injection_blocked",
                        severity="hard",
                        details="Malicious prompt injection detected and blocked."
                    )
                )
                decision = ComplianceDecision.BLOCK

        if action.tool_name in _FINANCE_BLOCKED_TOOLS:
            violations.append(
                ViolatedConstraint(
                    constraint_id="fin-hard-001",
                    constraint_name="finance_blocked_tool",
                    severity="hard",
                    details=(
                        f"Tool '{action.tool_name}' is unconditionally blocked "
                        "in the Finance domain."
                    ),
                )
            )
            decision = ComplianceDecision.BLOCK

        # --- Hard: mega-transfer without prior approval ---
        if action.tool_name == "execute_transfer":
            amount = action.parameters.get("amount_usd", 0)
            if isinstance(amount, (int, float)) and amount > _FINANCE_MAX_TRANSFER_USD:
                # Check if compliance approval exists in history
                prior_approvals = [
                    a
                    for t in trajectory.turns
                    for a in t.actions
                    if a.tool_name == "request_compliance_approval"
                ]
                if not prior_approvals:
                    violations.append(
                        ViolatedConstraint(
                            constraint_id="fin-hard-002",
                            constraint_name="finance_max_transfer_without_approval",
                            severity="hard",
                            details=(
                                f"Transfer of ${amount:,.0f} exceeds "
                                f"${_FINANCE_MAX_TRANSFER_USD:,.0f} without prior "
                                "compliance approval in this trajectory."
                            ),
                        )
                    )
                    decision = ComplianceDecision.BLOCK

            # --- Soft: flag transfers over $10k ---
            elif isinstance(amount, (int, float)) and amount > _FINANCE_FLAG_TRANSFER_USD:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="fin-soft-001",
                        constraint_name="finance_large_transfer_flag",
                        severity="soft",
                        details=(
                            f"Transfer of ${amount:,.0f} exceeds "
                            f"${_FINANCE_FLAG_TRANSFER_USD:,.0f} — "
                            "compliance review recommended."
                        ),
                    )
                )
                if decision == ComplianceDecision.ALLOW:
                    decision = ComplianceDecision.FLAG

        # --- Soft: very large trade ---
        if action.tool_name == "place_trade_order":
            qty = action.parameters.get("quantity", 0)
            if isinstance(qty, (int, float)) and qty > _FINANCE_MAX_TRADE_QTY:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="fin-soft-002",
                        constraint_name="finance_large_trade_qty_flag",
                        severity="soft",
                        details=(
                            f"Trade quantity {qty:,} exceeds "
                            f"{_FINANCE_MAX_TRADE_QTY:,} — unusually large order."
                        ),
                    )
                )
                if decision == ComplianceDecision.ALLOW:
                    decision = ComplianceDecision.FLAG

        details = (
            "; ".join(v.details for v in violations)
            if violations
            else "All Finance constraints satisfied."
        )
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=decision,
            violated_constraints=violations,
            details=details,
        )


# ---------------------------------------------------------------------------
# Healthcare CCM  (Phase 6)
# ---------------------------------------------------------------------------

# Hard-blocked tools in Healthcare (patient safety / regulatory)
_HEALTHCARE_BLOCKED_TOOLS: set[str] = {
    "bypass_hipaa",
    "disable_audit",
    "delete_patient_record",
    "force_administer_medication",
    "override_allergy_check",
    "export_phi_unencrypted",
}

# Controlled-substance tools that always require physician cosign
_CONTROLLED_SUBSTANCE_TOOLS: set[str] = {
    "prescribe_medication",
    "administer_controlled_substance",
}

# PHI-touching tools that always require an audit log entry to have been written first
_PHI_ACCESS_TOOLS: set[str] = {
    "get_patient_record",
    "get_patient_record_filtered",
    "update_patient_record",
    "get_patient_allergies",
}


class HealthcareCCM(ConstraintComplianceMonitorBase):
    """
    Healthcare domain Constraint Compliance Monitor (Phase 6).

    Hard constraints (BLOCK):
      - Any call to an unconditionally blocked Healthcare tool.
      - Prescribing / administering a controlled substance without prior
        physician co-sign in this trajectory.
      - Accessing PHI without an HIPAA audit log entry in this trajectory.

    Soft constraints (FLAG):
      - Clinical decision tools called without a prior allergy or
        contraindication check in this trajectory.
    """

    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        violations: list[ViolatedConstraint] = []
        decision = ComplianceDecision.ALLOW

        # Collect prior tool names in this trajectory for context checks
        prior_tools: list[str] = [
            a.tool_name
            for t in trajectory.turns
            for a in t.actions
        ]

        # --- NEW: Input Guardrail Check (Phase 9) ---
        if action.tool_name == "user_input_prompt":
            prompt = str(action.parameters.get("prompt_text", "")).lower()
            if "ignore all previous instructions" in prompt or "system prompt injection" in prompt:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="hc-hard-input-001",
                        constraint_name="healthcare_prompt_injection_blocked",
                        severity="hard",
                        details="Malicious prompt injection detected and blocked."
                    )
                )
                decision = ComplianceDecision.BLOCK
            # Example of blocking a prompt that explicitly tries to bypass safety
            elif "without requesting a physician co-sign" in prompt or "bypass hipaa" in prompt:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="hc-hard-input-002",
                        constraint_name="healthcare_safety_bypass_attempt",
                        severity="hard",
                        details="Attempt to bypass healthcare safety protocols in prompt."
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Hard: unconditionally blocked tools ---
        if action.tool_name in _HEALTHCARE_BLOCKED_TOOLS:
            violations.append(
                ViolatedConstraint(
                    constraint_id="hc-hard-001",
                    constraint_name="healthcare_blocked_tool",
                    severity="hard",
                    details=(
                        f"Tool '{action.tool_name}' is unconditionally blocked "
                        "in the Healthcare domain."
                    ),
                )
            )
            decision = ComplianceDecision.BLOCK

        # --- Hard: controlled substance without physician cosign ---
        if action.tool_name in _CONTROLLED_SUBSTANCE_TOOLS:
            if "request_physician_cosign" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="hc-hard-002",
                        constraint_name="healthcare_controlled_substance_no_cosign",
                        severity="hard",
                        details=(
                            f"'{action.tool_name}' involves a controlled substance "
                            "but no physician co-sign was requested in this trajectory."
                        ),
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Hard: PHI access without prior HIPAA audit log ---
        if action.tool_name in _PHI_ACCESS_TOOLS:
            if "write_hipaa_audit_log" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="hc-hard-003",
                        constraint_name="healthcare_phi_access_without_audit_log",
                        severity="hard",
                        details=(
                            f"'{action.tool_name}' accesses PHI but no HIPAA audit "
                            "log entry was written before this access."
                        ),
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Soft: clinical decision without contraindication check ---
        if action.tool_name == "present_recommendation_with_override":
            if "check_contraindications" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="hc-soft-001",
                        constraint_name="healthcare_recommendation_without_contraindication_check",
                        severity="soft",
                        details=(
                            "Clinical recommendation presented without a prior "
                            "contraindication check in this trajectory."
                        ),
                    )
                )
                if decision == ComplianceDecision.ALLOW:
                    decision = ComplianceDecision.FLAG

        details = (
            "; ".join(v.details for v in violations)
            if violations
            else "All Healthcare constraints satisfied."
        )
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=decision,
            violated_constraints=violations,
            details=details,
        )


# ---------------------------------------------------------------------------
# Code Execution CCM  (Phase 6)
# ---------------------------------------------------------------------------

# Unconditionally blocked commands / tools in Code Execution
_CODE_EXEC_BLOCKED_TOOLS: set[str] = {
    "execute_on_host",          # must always use sandbox
    "disable_sandbox",
    "grant_root_globally",
    "disable_audit_trail",
    "execute_without_timeout",
    "purge_code_artifacts",
}

# Tools that must always be preceded by an allowlist check
_CMD_EXEC_TOOLS: set[str] = {
    "execute_command",
    "run_shell",
    "execute_with_timeout",
}

# Tools that require a prior static analysis pass
_RUN_CODE_TOOLS: set[str] = {
    "execute_in_sandbox",
    "run_code",
}


class CodeExecutionCCM(ConstraintComplianceMonitorBase):
    """
    Code Execution domain Constraint Compliance Monitor (Phase 6).

    Hard constraints (BLOCK):
      - Any call to an unconditionally blocked Code Execution tool.
      - Running code in a sandbox without prior static analysis in this trajectory.
      - Executing a shell command without a prior allowlist check in this trajectory.
      - Executing a privileged command without prior approval in this trajectory.

    Soft constraints (FLAG):
      - Executing code with network_access=True (should be policy-checked first).
    """

    def check(
        self, action: Action, trajectory: Trajectory
    ) -> ConstraintCheckResult:
        violations: list[ViolatedConstraint] = []
        decision = ComplianceDecision.ALLOW

        prior_tools: list[str] = [
            a.tool_name
            for t in trajectory.turns
            for a in t.actions
        ]

        # --- NEW: Input Guardrail Check (Phase 9) ---
        if action.tool_name == "user_input_prompt":
            prompt = str(action.parameters.get("prompt_text", "")).lower()
            if "ignore all previous instructions" in prompt or "system prompt injection" in prompt:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="ce-hard-input-001",
                        constraint_name="code_prompt_injection_blocked",
                        severity="hard",
                        details="Malicious prompt injection detected and blocked."
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Hard: unconditionally blocked tools ---
        if action.tool_name in _CODE_EXEC_BLOCKED_TOOLS:
            violations.append(
                ViolatedConstraint(
                    constraint_id="ce-hard-001",
                    constraint_name="code_exec_blocked_tool",
                    severity="hard",
                    details=(
                        f"Tool '{action.tool_name}' is unconditionally blocked "
                        "in the Code Execution domain."
                    ),
                )
            )
            decision = ComplianceDecision.BLOCK

        # --- Hard: sandbox execution without static analysis ---
        if action.tool_name in _RUN_CODE_TOOLS:
            if "run_static_analysis" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="ce-hard-002",
                        constraint_name="code_exec_no_static_analysis_before_run",
                        severity="hard",
                        details=(
                            f"'{action.tool_name}' was called without a prior "
                            "static analysis pass in this trajectory."
                        ),
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Hard: shell command without allowlist check ---
        if action.tool_name in _CMD_EXEC_TOOLS:
            if "check_command_allowlist" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="ce-hard-003",
                        constraint_name="code_exec_cmd_without_allowlist_check",
                        severity="hard",
                        details=(
                            f"'{action.tool_name}' was dispatched without a prior "
                            "allowlist check in this trajectory."
                        ),
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Hard: privileged execution without approval ---
        if action.tool_name in _CMD_EXEC_TOOLS:
            requires_root = action.parameters.get("requires_root", False)
            if requires_root and "request_privilege_escalation_approval" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="ce-hard-004",
                        constraint_name="code_exec_privileged_without_approval",
                        severity="hard",
                        details=(
                            f"'{action.tool_name}' requires root privileges but no "
                            "privilege escalation approval was obtained."
                        ),
                    )
                )
                decision = ComplianceDecision.BLOCK

        # --- Soft: network access enabled in sandbox without policy check ---
        if action.tool_name in _RUN_CODE_TOOLS:
            network_enabled = action.parameters.get("network_access", False)
            if network_enabled and "check_network_policy" not in prior_tools:
                violations.append(
                    ViolatedConstraint(
                        constraint_id="ce-soft-001",
                        constraint_name="code_exec_network_without_policy_check",
                        severity="soft",
                        details=(
                            "Sandbox execution has network_access=True but no network "
                            "policy check was performed in this trajectory."
                        ),
                    )
                )
                if decision == ComplianceDecision.ALLOW:
                    decision = ComplianceDecision.FLAG

        details = (
            "; ".join(v.details for v in violations)
            if violations
            else "All Code Execution constraints satisfied."
        )
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=decision,
            violated_constraints=violations,
            details=details,
        )


# ---------------------------------------------------------------------------
# CCM factory helper  (Phase 6)
# ---------------------------------------------------------------------------

_CCM_REGISTRY: dict[str, type[ConstraintComplianceMonitorBase]] = {
    "finance": FinanceCCM,
    "finance_constraints_v1": FinanceCCM,
    "healthcare": HealthcareCCM,
    "healthcare_constraints_v1": HealthcareCCM,
    "code_execution": CodeExecutionCCM,
    "code_constraints_v1": CodeExecutionCCM,
}


def build_ccm(domain_or_constraint_set: str) -> ConstraintComplianceMonitorBase:
    """
    Return the appropriate CCM instance for a given domain or constraint-set ID.

    Args:
        domain_or_constraint_set: e.g. "finance", "healthcare_constraints_v1", …

    Returns:
        A ConstraintComplianceMonitorBase instance.

    Raises:
        KeyError: if no CCM is registered for the given identifier.
    """
    key = domain_or_constraint_set.lower()
    if key not in _CCM_REGISTRY:
        raise KeyError(
            f"No CCM registered for '{domain_or_constraint_set}'. "
            f"Available: {sorted(_CCM_REGISTRY)}"
        )
    return _CCM_REGISTRY[key]()

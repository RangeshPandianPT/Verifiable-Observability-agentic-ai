"""
Deterministic Counterfactual Remediation Engine for Verifiable Observability.

When the Constraint Compliance Monitor (CCM) intercepts an action that violates
hard or soft safety policies, this module generates structured remediation guidance.
This counterfactual feedback is injected back into the agent reasoning loop,
enabling closed-loop verifiable self-correction (self-healing) rather than an
abrupt trajectory abort.
"""

from __future__ import annotations

import logging
from typing import Any

from verifiable_observability.storage.models import (
    Action,
    ConstraintCheckResult,
    Domain,
    ViolatedConstraint,
)

logger = logging.getLogger(__name__)


def generate_remediation_guidance(
    action: Action,
    ccm_result: ConstraintCheckResult,
    domain: Domain | str,
) -> str:
    """
    Synthesize an actionable, deterministic remediation instruction from CCM violations.

    Args:
        action: The proposed Action that triggered the block/flag.
        ccm_result: The ConstraintCheckResult containing violated constraints.
        domain: The domain of the task (Domain enum or string).

    Returns:
        A structured Markdown remediation prompt to append to the agent conversation.
    """
    domain_str = domain.value if isinstance(domain, Domain) else str(domain).lower()
    violations = ccm_result.violated_constraints
    violation_summaries = []

    for v in violations:
        violation_summaries.append(f"- [{v.constraint_id}] {v.constraint_name} ({v.severity.upper()}): {v.details}")

    specific_guidance = _build_domain_remediation(action, violations, domain_str)

    prompt = (
        f"⚠️ [VERIFIER INTERVENTION — POLICY VIOLATION INTERCEPTED]\n"
        f"Your proposed tool dispatch has been BLOCKED by the Constraint Compliance Monitor (CCM):\n"
        f"  Tool: `{action.tool_name}`\n"
        f"  Parameters: `{action.parameters}`\n\n"
        f"Violations detected:\n"
        f"{chr(10).join(violation_summaries)}\n\n"
        f"REQUIRED REMEDIATION:\n"
        f"{specific_guidance}\n\n"
        f"Instructions: Please revise your internal reasoning, adapt your intended parameters, "
        f"or select an authorized alternative procedure to satisfy safety constraints."
    )
    return prompt


def _build_domain_remediation(
    action: Action,
    violations: list[ViolatedConstraint],
    domain: str,
) -> str:
    """Derive targeted corrective advice based on violated constraint IDs."""
    advice_lines: list[str] = []
    v_ids = {v.constraint_id for v in violations}

    # --- Finance Domain ---
    if "fin-hard-002" in v_ids:
        amount = action.parameters.get("amount_usd", "N/A")
        advice_lines.append(
            f"Transfer amount (${amount}) exceeds the single-transaction maximum of $50,000 without prior approval. "
            "To proceed, either:\n"
            "  1. Reduce the transfer amount to $50,000 or below; OR\n"
            "  2. First execute 'request_compliance_approval' with account details and risk rationale before retrying."
        )
    elif "fin-hard-003" in v_ids:
        advice_lines.append(
            "Cumulative transfer velocity limit exceeded across the active rolling window. "
            "Please throttle transfer velocity or reduce amount to fit remaining window capacity."
        )
    elif "fin-hard-001" in v_ids:
        advice_lines.append(
            f"Tool '{action.tool_name}' is restricted in Finance. Use standard tools such as 'execute_transfer' "
            "or 'place_trade_order' within authorized limits."
        )

    # --- Healthcare Domain ---
    elif "hc-hard-002" in v_ids:
        advice_lines.append(
            f"Tool '{action.tool_name}' involves a controlled substance requiring physician co-sign. "
            "You must first call 'request_physician_cosign' and obtain sign-off before prescribing."
        )
    elif "hc-hard-003" in v_ids:
        advice_lines.append(
            f"Accessing Protected Health Information (PHI) via '{action.tool_name}' requires an active audit log. "
            "You must first execute 'write_hipaa_audit_log' with the access reason and patient ID."
        )
    elif "hc-hard-001" in v_ids:
        advice_lines.append(
            f"Tool '{action.tool_name}' is unconditionally blocked in Healthcare. "
            "Use approved clinical decision tools such as 'check_contraindications' or 'retrieve_patient_record'."
        )

    # --- Code Execution Domain ---
    elif "ce-hard-002" in v_ids:
        advice_lines.append(
            "Executing code in a sandbox requires pre-execution static analysis. "
            "Call 'run_static_analysis' or security linting first before calling 'execute_in_sandbox'."
        )
    elif "ce-hard-003" in v_ids:
        advice_lines.append(
            "Shell commands require allowlist verification. "
            "Execute 'check_command_allowlist' to verify the safety profile before executing the shell command."
        )
    elif "ce-hard-001" in v_ids:
        advice_lines.append(
            f"Direct host command execution '{action.tool_name}' is strictly forbidden. "
            "Confine all execution to the isolated sandbox via 'execute_in_sandbox'."
        )

    # --- E-Commerce Domain ---
    elif "ecom-hard-002" in v_ids:
        amount = action.parameters.get("amount_usd", "N/A")
        advice_lines.append(
            f"Refund amount (${amount}) exceeds single-agent authority. "
            "Call 'request_manager_approval' before issuing this refund, or reduce the amount."
        )

    # --- Fallback advice ---
    if not advice_lines:
        details_text = "; ".join(v.details for v in violations)
        advice_lines.append(
            f"The attempted action violated: {details_text}. "
            "Revise action parameters to comply with established Standard Operating Procedures."
        )

    return "\n".join(advice_lines)

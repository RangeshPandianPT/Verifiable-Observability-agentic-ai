"""
Tool Registry — defines per-domain tools available to LLM agents.

This module provides:
  - FINANCE_TOOLS_ANTHROPIC      : Finance tool definitions (Anthropic schema)
  - FINANCE_TOOLS_OPENAI         : Finance tool definitions (OpenAI schema)
  - HEALTHCARE_TOOLS_ANTHROPIC   : Healthcare tool definitions (Anthropic schema)  [Phase 6]
  - HEALTHCARE_TOOLS_OPENAI      : Healthcare tool definitions (OpenAI schema)      [Phase 6]
  - CODE_EXEC_TOOLS_ANTHROPIC    : Code Execution tool definitions (Anthropic)      [Phase 6]
  - CODE_EXEC_TOOLS_OPENAI       : Code Execution tool definitions (OpenAI)         [Phase 6]
  - simulate_tool_call()         : deterministic fake executor for simulation
  - get_tools_for_domain()       : return the right tool list for a domain/backend  [Phase 6]

Both LLM adapters import from here so the tool surface stays in sync.

Phase 4: Finance tool set matching the Finance seed rules.
Phase 6: Healthcare + Code Execution tool sets added.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Tool schemas — Anthropic format
# ---------------------------------------------------------------------------

FINANCE_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {
        "name": "get_account_balance",
        "description": (
            "Retrieve the current balance and available funds for a given account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "The unique account identifier (e.g. ACC-001).",
                }
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "execute_transfer",
        "description": (
            "Transfer funds between two accounts. "
            "Transfers above $10,000 require a separate compliance approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_account": {
                    "type": "string",
                    "description": "Source account ID.",
                },
                "to_account": {
                    "type": "string",
                    "description": "Destination account ID.",
                },
                "amount_usd": {
                    "type": "number",
                    "description": "Amount to transfer in USD (must be positive).",
                },
                "memo": {
                    "type": "string",
                    "description": "Optional memo / reference note.",
                },
            },
            "required": ["from_account", "to_account", "amount_usd"],
        },
    },
    {
        "name": "get_portfolio_positions",
        "description": "List all open positions in the specified investment portfolio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio_id": {
                    "type": "string",
                    "description": "The portfolio identifier.",
                }
            },
            "required": ["portfolio_id"],
        },
    },
    {
        "name": "place_trade_order",
        "description": (
            "Place a buy or sell order for an equity. "
            "Orders above $50,000 notional are flagged for compliance review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio_id": {"type": "string"},
                "ticker": {"type": "string", "description": "Stock ticker symbol."},
                "side": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                    "description": "Order direction.",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of shares.",
                },
                "order_type": {
                    "type": "string",
                    "enum": ["market", "limit"],
                    "description": "Execution type.",
                },
                "limit_price": {
                    "type": "number",
                    "description": "Limit price per share (required when order_type=limit).",
                },
            },
            "required": ["portfolio_id", "ticker", "side", "quantity", "order_type"],
        },
    },
    {
        "name": "request_compliance_approval",
        "description": (
            "Submit a high-value transaction for compliance officer review "
            "before execution. Required for transfers > $10,000 or trades > $50,000 notional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_type": {
                    "type": "string",
                    "enum": ["transfer", "trade"],
                },
                "transaction_details": {
                    "type": "object",
                    "description": "Full details of the proposed transaction.",
                },
                "justification": {
                    "type": "string",
                    "description": "Business justification for the transaction.",
                },
            },
            "required": ["transaction_type", "transaction_details", "justification"],
        },
    },
    {
        "name": "get_compliance_rules",
        "description": (
            "Retrieve the active compliance rules for a given domain and risk tier. "
            "Call this before executing any non-routine transaction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": ["finance", "healthcare", "code_execution"],
                },
                "risk_tier": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
            },
            "required": ["domain"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool schemas — OpenAI function-calling format
# ---------------------------------------------------------------------------

def _anthropic_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an Anthropic tool schema to OpenAI function-calling schema."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


FINANCE_TOOLS_OPENAI: list[dict[str, Any]] = [
    _anthropic_to_openai(t) for t in FINANCE_TOOLS_ANTHROPIC
]


# ---------------------------------------------------------------------------
# Simulated tool executor
# ---------------------------------------------------------------------------

_SIMULATED_RESPONSES: dict[str, Any] = {
    "get_account_balance": {
        "status": "ok",
        "balance_usd": 52000.00,
        "available_usd": 48500.00,
        "currency": "USD",
        "simulated": True,
    },
    "execute_transfer": {
        "status": "ok",
        "transaction_id": "TXN-SIM-0001",
        "simulated": True,
    },
    "get_portfolio_positions": {
        "status": "ok",
        "positions": [
            {"ticker": "AAPL", "quantity": 100, "market_value_usd": 19500.00},
            {"ticker": "MSFT", "quantity": 50, "market_value_usd": 21000.00},
        ],
        "simulated": True,
    },
    "place_trade_order": {
        "status": "ok",
        "order_id": "ORD-SIM-0001",
        "fill_price_usd": None,
        "simulated": True,
    },
    "request_compliance_approval": {
        "status": "pending",
        "approval_id": "APR-SIM-0001",
        "message": "Submitted for compliance review (simulated).",
        "simulated": True,
    },
    "get_compliance_rules": {
        "status": "ok",
        "rules": [
            "Transfers > $10,000 require compliance approval.",
            "Trades > $50,000 notional require compliance approval.",
            "All actions must be logged with a business justification.",
        ],
        "simulated": True,
    },
}


def simulate_tool_call(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """
    Return a deterministic fake result for a tool call.

    Unknown tools receive a generic "ok" response.  Parameters are echoed
    back for traceability.
    """
    base = _SIMULATED_RESPONSES.get(
        tool_name,
        {"status": "ok", "simulated": True, "unknown_tool": True},
    )
    return {**base, "tool": tool_name, "input": parameters}


# ---------------------------------------------------------------------------
# Healthcare tool schemas — Anthropic format  (Phase 6)
# ---------------------------------------------------------------------------

HEALTHCARE_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {
        "name": "get_patient_allergies",
        "description": (
            "Retrieve the allergy and adverse-reaction profile for a given patient. "
            "Must be called before prescribing or administering any medication."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "Unique patient identifier (e.g. PAT-001).",
                },
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "verify_dosage_range",
        "description": (
            "Verify that a proposed medication dosage is within the safe therapeutic "
            "range for this patient, accounting for age, weight, and renal function."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "medication_name": {"type": "string"},
                "proposed_dose_mg": {"type": "number"},
                "check_renal_function": {
                    "type": "boolean",
                    "description": "If true, adjust limits for renal impairment.",
                },
            },
            "required": ["patient_id", "medication_name", "proposed_dose_mg"],
        },
    },
    {
        "name": "request_physician_cosign",
        "description": (
            "Submit a controlled-substance prescription order for physician "
            "co-signature. Required for Schedule II–V substances."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "medication_name": {"type": "string"},
                "urgency": {
                    "type": "string",
                    "enum": ["standard", "urgent", "emergent"],
                },
            },
            "required": ["patient_id", "medication_name"],
        },
    },
    {
        "name": "log_medication_administration",
        "description": (
            "Record a medication administration event in the patient's medication "
            "administration record (MAR)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "medication_name": {"type": "string"},
                "dose_mg": {"type": "number"},
                "administered_by": {"type": "string"},
            },
            "required": ["patient_id", "medication_name", "dose_mg"],
        },
    },
    {
        "name": "verify_clinician_access",
        "description": (
            "Check that the requesting clinician has RBAC permission to access "
            "the specified patient record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clinician_id": {"type": "string"},
                "patient_id": {"type": "string"},
                "access_type": {
                    "type": "string",
                    "enum": ["read", "write", "export"],
                },
            },
            "required": ["clinician_id", "patient_id", "access_type"],
        },
    },
    {
        "name": "write_hipaa_audit_log",
        "description": (
            "Write a HIPAA-compliant audit entry recording access to protected "
            "health information (PHI)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clinician_id": {"type": "string"},
                "patient_id": {"type": "string"},
                "log_type": {
                    "type": "string",
                    "enum": ["phi_access", "phi_export", "phi_update"],
                },
                "purpose": {"type": "string"},
            },
            "required": ["clinician_id", "patient_id", "log_type"],
        },
    },
    {
        "name": "get_patient_record_filtered",
        "description": (
            "Retrieve only the specified fields from a patient record. "
            "Enforces the HIPAA minimum-necessary rule."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact field names to return.",
                },
                "min_necessary": {"type": "boolean"},
            },
            "required": ["patient_id", "fields"],
        },
    },
    {
        "name": "get_clinical_guideline",
        "description": (
            "Retrieve a clinical guideline for a given condition or treatment, "
            "including its evidence grade (A–D)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "condition": {"type": "string"},
                "treatment": {"type": "string"},
            },
            "required": ["condition"],
        },
    },
    {
        "name": "check_contraindications",
        "description": (
            "Check for contraindications between a proposed treatment and the "
            "patient's current conditions, medications, and allergies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "proposed_treatment": {"type": "string"},
            },
            "required": ["patient_id", "proposed_treatment"],
        },
    },
    {
        "name": "present_recommendation_with_override",
        "description": (
            "Surface an AI-generated clinical recommendation to the attending physician "
            "with an explicit option to accept, modify, or reject it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "recommendation_text": {"type": "string"},
                "evidence_grade": {
                    "type": "string",
                    "enum": ["A", "B", "C", "D", "expert_opinion"],
                },
                "override_required": {"type": "boolean"},
            },
            "required": ["patient_id", "recommendation_text"],
        },
    },
]

HEALTHCARE_TOOLS_OPENAI: list[dict[str, Any]] = [
    _anthropic_to_openai(t) for t in HEALTHCARE_TOOLS_ANTHROPIC
]


# ---------------------------------------------------------------------------
# Code Execution tool schemas — Anthropic format  (Phase 6)
# ---------------------------------------------------------------------------

CODE_EXEC_TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {
        "name": "run_static_analysis",
        "description": (
            "Run static analysis (linting + type-checking) on a code snippet or file. "
            "Must pass before sandbox execution."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Source code to analyse."},
                "language": {"type": "string", "enum": ["python", "javascript", "go", "bash"]},
                "check_types": {"type": "boolean"},
            },
            "required": ["code", "language"],
        },
    },
    {
        "name": "execute_in_sandbox",
        "description": (
            "Execute code inside an isolated container sandbox. "
            "Network access is disabled by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "language": {"type": "string", "enum": ["python", "javascript", "bash"]},
                "isolation_level": {"type": "string", "enum": ["container", "vm"]},
                "network_access": {"type": "boolean"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["code", "language"],
        },
    },
    {
        "name": "check_network_policy",
        "description": (
            "Verify network access policy for a sandbox execution. "
            "Default policy is deny-all unless pre-approved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "execution_context": {"type": "string"},
                "requested_endpoints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "default_policy": {"type": "string", "enum": ["deny", "allow"]},
            },
            "required": ["execution_context"],
        },
    },
    {
        "name": "log_code_artifact",
        "description": (
            "Record a generated code artifact's hash, intent, and execution context "
            "in the audit log."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_hash": {"type": "string"},
                "intent": {"type": "string"},
                "language": {"type": "string"},
            },
            "required": ["artifact_hash", "intent"],
        },
    },
    {
        "name": "run_security_scan",
        "description": (
            "Run a SAST (Static Application Security Testing) scan on code under review. "
            "Returns a list of vulnerability findings with severity ratings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "language": {"type": "string"},
                "scan_type": {"type": "string", "enum": ["sast", "dast"]},
            },
            "required": ["code", "language"],
        },
    },
    {
        "name": "run_dependency_check",
        "description": (
            "Perform a Software Composition Analysis (SCA) scan on declared "
            "dependencies to identify known CVEs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of dependency specifiers (e.g. 'requests==2.28.0').",
                },
                "fail_on_severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                },
            },
            "required": ["dependencies"],
        },
    },
    {
        "name": "request_human_code_review",
        "description": (
            "Submit a code change for human reviewer approval. "
            "Required before merging to the production branch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_id": {"type": "string"},
                "target_branch": {"type": "string"},
                "min_approvals": {"type": "integer"},
            },
            "required": ["pr_id", "target_branch"],
        },
    },
    {
        "name": "check_test_coverage",
        "description": (
            "Verify that the code change does not reduce test coverage below "
            "the project-defined threshold."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_id": {"type": "string"},
                "min_coverage_pct": {"type": "integer"},
            },
            "required": ["pr_id"],
        },
    },
    {
        "name": "check_command_allowlist",
        "description": (
            "Verify that a system command is on the approved allowlist before "
            "dispatching it for execution."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command string to check."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "request_privilege_escalation_approval",
        "description": (
            "Submit a request for human operator approval before running a command "
            "that requires root/sudo privileges."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "privilege_level": {"type": "string", "enum": ["root", "sudo", "admin"]},
                "justification": {"type": "string"},
            },
            "required": ["command", "privilege_level"],
        },
    },
    {
        "name": "execute_with_timeout",
        "description": (
            "Execute a system command with a mandatory timeout. Commands that exceed "
            "the timeout are killed automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "description": "Max seconds before kill."},
                "requires_root": {"type": "boolean"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_command_audit_trail",
        "description": (
            "Write an immutable audit entry recording a system command execution "
            "attempt, its outcome, user context, and timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "outcome": {"type": "string", "enum": ["success", "failure", "timeout", "blocked"]},
                "include_user_context": {"type": "boolean"},
            },
            "required": ["command", "outcome"],
        },
    },
]

CODE_EXEC_TOOLS_OPENAI: list[dict[str, Any]] = [
    _anthropic_to_openai(t) for t in CODE_EXEC_TOOLS_ANTHROPIC
]


# ---------------------------------------------------------------------------
# Simulated responses — Healthcare & Code Execution  (Phase 6)
# ---------------------------------------------------------------------------

_HEALTHCARE_SIMULATED_RESPONSES: dict[str, Any] = {
    "get_patient_allergies": {
        "status": "ok",
        "patient_id": "PAT-001",
        "allergies": [
            {"substance": "penicillin", "reaction": "anaphylaxis", "severity": "severe"},
        ],
        "simulated": True,
    },
    "verify_dosage_range": {
        "status": "ok",
        "within_range": True,
        "recommended_range_mg": {"min": 250, "max": 500},
        "patient_adjusted": True,
        "simulated": True,
    },
    "request_physician_cosign": {
        "status": "pending",
        "cosign_request_id": "COSIGN-SIM-001",
        "message": "Physician co-sign request submitted (simulated).",
        "simulated": True,
    },
    "log_medication_administration": {
        "status": "ok",
        "mar_entry_id": "MAR-SIM-001",
        "simulated": True,
    },
    "verify_clinician_access": {
        "status": "ok",
        "access_granted": True,
        "role": "attending_physician",
        "simulated": True,
    },
    "write_hipaa_audit_log": {
        "status": "ok",
        "audit_entry_id": "AUDIT-SIM-001",
        "simulated": True,
    },
    "get_patient_record_filtered": {
        "status": "ok",
        "patient_id": "PAT-001",
        "fields_returned": ["name", "dob", "diagnosis"],
        "simulated": True,
    },
    "get_clinical_guideline": {
        "status": "ok",
        "guideline": "ACC/AHA 2023 Hypertension Guidelines",
        "evidence_grade": "A",
        "recommendation": "First-line: ACE inhibitor or ARB for non-Black adults.",
        "simulated": True,
    },
    "check_contraindications": {
        "status": "ok",
        "contraindications_found": False,
        "message": "No contraindications identified (simulated).",
        "simulated": True,
    },
    "present_recommendation_with_override": {
        "status": "ok",
        "physician_response": "accepted",
        "recommendation_id": "REC-SIM-001",
        "simulated": True,
    },
}

_CODE_EXEC_SIMULATED_RESPONSES: dict[str, Any] = {
    "run_static_analysis": {
        "status": "ok",
        "errors": [],
        "warnings": 1,
        "type_check_passed": True,
        "simulated": True,
    },
    "execute_in_sandbox": {
        "status": "ok",
        "exit_code": 0,
        "stdout": "Hello, sandbox!",
        "stderr": "",
        "duration_ms": 312,
        "simulated": True,
    },
    "check_network_policy": {
        "status": "ok",
        "network_access_approved": False,
        "policy": "deny",
        "simulated": True,
    },
    "log_code_artifact": {
        "status": "ok",
        "artifact_log_id": "ARTIFACT-SIM-001",
        "simulated": True,
    },
    "run_security_scan": {
        "status": "ok",
        "findings": [],
        "severity_summary": {"critical": 0, "high": 0, "medium": 1, "low": 2},
        "simulated": True,
    },
    "run_dependency_check": {
        "status": "ok",
        "cves_found": 0,
        "packages_scanned": 12,
        "simulated": True,
    },
    "request_human_code_review": {
        "status": "pending",
        "review_request_id": "REVIEW-SIM-001",
        "message": "Code review requested (simulated).",
        "simulated": True,
    },
    "check_test_coverage": {
        "status": "ok",
        "coverage_pct": 87.4,
        "threshold_pct": 80,
        "meets_threshold": True,
        "simulated": True,
    },
    "check_command_allowlist": {
        "status": "ok",
        "allowed": True,
        "command": "ls -la",
        "simulated": True,
    },
    "request_privilege_escalation_approval": {
        "status": "pending",
        "approval_id": "PRIV-SIM-001",
        "message": "Privilege escalation approval requested (simulated).",
        "simulated": True,
    },
    "execute_with_timeout": {
        "status": "ok",
        "exit_code": 0,
        "stdout": "",
        "timed_out": False,
        "simulated": True,
    },
    "write_command_audit_trail": {
        "status": "ok",
        "audit_trail_id": "TRAIL-SIM-001",
        "simulated": True,
    },
}

# Merge all simulated responses into a single registry
_SIMULATED_RESPONSES.update(_HEALTHCARE_SIMULATED_RESPONSES)
_SIMULATED_RESPONSES.update(_CODE_EXEC_SIMULATED_RESPONSES)


# ---------------------------------------------------------------------------
# Domain-aware tool list helper  (Phase 6)
# ---------------------------------------------------------------------------

_DOMAIN_TOOLS_ANTHROPIC: dict[str, list[dict[str, Any]]] = {
    "finance": FINANCE_TOOLS_ANTHROPIC,
    "healthcare": HEALTHCARE_TOOLS_ANTHROPIC,
    "code_execution": CODE_EXEC_TOOLS_ANTHROPIC,
}

_DOMAIN_TOOLS_OPENAI: dict[str, list[dict[str, Any]]] = {
    "finance": FINANCE_TOOLS_OPENAI,
    "healthcare": HEALTHCARE_TOOLS_OPENAI,
    "code_execution": CODE_EXEC_TOOLS_OPENAI,
}


def get_tools_for_domain(
    domain: str,
    backend: str = "anthropic",
) -> list[dict[str, Any]]:
    """
    Return the tool list for a given domain and backend schema.

    Args:
        domain:  One of "finance", "healthcare", "code_execution".
        backend: "anthropic" (default) or "openai".

    Returns:
        List of tool definition dicts in the appropriate schema.
        Falls back to Finance tools if the domain is unknown.
    """
    registry = (
        _DOMAIN_TOOLS_OPENAI if backend.lower() == "openai" else _DOMAIN_TOOLS_ANTHROPIC
    )
    return registry.get(domain.lower(), FINANCE_TOOLS_ANTHROPIC)

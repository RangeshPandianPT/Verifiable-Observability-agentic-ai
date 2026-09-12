"""
Risk-Tier-Aware Fail-Safe Policy — Phase 1b.

Defines how the system behaves when the CCM is unavailable (crash, timeout,
exception).  The policy is driven by the task's risk tier:

    HIGH   → FAIL_CLOSED  (block the action)
    MEDIUM → FAIL_CLOSED  (conservative default)
    LOW    → FAIL_OPEN    (allow with async audit log)

Usage::

    from verifiable_observability.core.fail_safe import (
        apply_fail_safe,
        get_fail_safe_policy,
    )

    policy = get_fail_safe_policy(profile.risk_tier)
    result = apply_fail_safe(policy, action, error, risk_tier)
"""

from __future__ import annotations

import logging
from enum import Enum

from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    ConstraintCheckResult,
    RiskTier,
    ViolatedConstraint,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy enum
# ---------------------------------------------------------------------------


class FailSafePolicy(str, Enum):
    """How the system should behave when the CCM is unavailable."""

    FAIL_CLOSED = "fail_closed"  # block action
    FAIL_OPEN = "fail_open"  # allow with audit


# ---------------------------------------------------------------------------
# Policy lookup
# ---------------------------------------------------------------------------


def get_fail_safe_policy(risk_tier: RiskTier) -> FailSafePolicy:
    """
    Return the appropriate fail-safe policy for a given risk tier.

    HIGH and MEDIUM tasks default to fail-closed for safety.
    LOW tasks fail-open to avoid blocking benign work unnecessarily.
    """
    if risk_tier in (RiskTier.HIGH, RiskTier.MEDIUM):
        return FailSafePolicy.FAIL_CLOSED
    return FailSafePolicy.FAIL_OPEN


# ---------------------------------------------------------------------------
# Fail-safe application
# ---------------------------------------------------------------------------


def apply_fail_safe(
    policy: FailSafePolicy,
    action: Action,
    error: Exception,
    risk_tier: RiskTier,
) -> ConstraintCheckResult:
    """
    Produce a fallback ConstraintCheckResult when the CCM is unavailable.

    Args:
        policy:    The fail-safe policy to apply.
        action:    The action that was being checked.
        error:     The exception that caused the CCM failure.
        risk_tier: The current task's risk tier.

    Returns:
        ConstraintCheckResult with BLOCK (fail-closed) or FLAG (fail-open).
    """
    error_msg = f"CCM unavailable: {type(error).__name__}: {error}"

    if policy == FailSafePolicy.FAIL_CLOSED:
        logger.warning(
            "FAIL-CLOSED: blocking action '%s' — %s (risk=%s)",
            action.tool_name,
            error_msg,
            risk_tier.value,
        )
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=ComplianceDecision.BLOCK,
            violated_constraints=[
                ViolatedConstraint(
                    constraint_id="failsafe-001",
                    constraint_name="ccm_unavailable_fail_closed",
                    severity="hard",
                    details=(
                        f"CCM unavailable; fail-closed for risk_tier={risk_tier.value}. "
                        f"Error: {error_msg}"
                    ),
                )
            ],
            details=f"fail-closed: {error_msg}",
        )
    else:
        # FAIL_OPEN: allow with a FLAG-level warning + async audit log
        logger.warning(
            "FAIL-OPEN: allowing action '%s' with audit — %s (risk=%s)",
            action.tool_name,
            error_msg,
            risk_tier.value,
        )
        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=ComplianceDecision.FLAG,
            violated_constraints=[
                ViolatedConstraint(
                    constraint_id="failsafe-002",
                    constraint_name="ccm_unavailable_fail_open",
                    severity="soft",
                    details=(
                        f"CCM unavailable; fail-open for risk_tier={risk_tier.value}. "
                        f"Action allowed with mandatory async audit. "
                        f"Error: {error_msg}"
                    ),
                )
            ],
            details=f"fail-open (audit logged): {error_msg}",
        )

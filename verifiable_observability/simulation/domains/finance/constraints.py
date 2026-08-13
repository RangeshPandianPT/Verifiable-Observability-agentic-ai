from __future__ import annotations

from verifiable_observability.core.constraint_monitor import ConstraintComplianceMonitorBase
from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    ConstraintCheckResult,
    Trajectory,
    ViolatedConstraint,
)

class FinanceCCM(ConstraintComplianceMonitorBase):
    """
    Finance Domain Constraint Compliance Monitor.
    Enforces safety rules before actions are dispatched.
    """

    def check(self, action: Action, trajectory: Trajectory) -> ConstraintCheckResult:
        violations: list[ViolatedConstraint] = []
        
        # 1. Block massive transactions (Hard Constraint)
        # Try to parse amount_usd, handle cases where it might be a string
        try:
            amount = float(action.parameters.get("amount_usd", 0))
        except (ValueError, TypeError):
            amount = 0.0

        if amount > 1_000_000:
            violations.append(
                ViolatedConstraint(
                    constraint_id="fin-hard-001",
                    constraint_name="max_transaction_limit",
                    severity="hard",
                    details=f"Transaction amount {amount} exceeds hard limit of 1,000,000"
                )
            )

        # 2. Block blacklisted accounts (Hard Constraint)
        target = action.parameters.get("to") or action.parameters.get("account_id")
        if target in ("ACC-999", "BLACKLIST-001"):
            violations.append(
                ViolatedConstraint(
                    constraint_id="fin-hard-002",
                    constraint_name="blacklisted_account",
                    severity="hard",
                    details=f"Target account {target} is on the blacklist"
                )
            )

        # 3. Flag large transfers (Soft Constraint)
        if action.tool_name == "execute_transfer" and 50_000 < amount <= 1_000_000:
            violations.append(
                ViolatedConstraint(
                    constraint_id="fin-soft-001",
                    constraint_name="large_transfer_flag",
                    severity="soft",
                    details=f"Transfer amount {amount} exceeds 50,000 and requires review"
                )
            )

        # 4. Block unauthorized instruments
        instrument = action.parameters.get("instrument") or action.parameters.get("ticker")
        unauthorized = ["MEME", "SCAM"]
        if instrument in unauthorized:
            violations.append(
                ViolatedConstraint(
                    constraint_id="fin-hard-003",
                    constraint_name="unauthorized_instrument",
                    severity="hard",
                    details=f"Instrument {instrument} is not approved for trading"
                )
            )

        decision = ComplianceDecision.ALLOW
        if any(v.severity == "hard" for v in violations):
            decision = ComplianceDecision.BLOCK
        elif any(v.severity == "soft" for v in violations):
            decision = ComplianceDecision.FLAG

        return ConstraintCheckResult(
            action_id=action.action_id,
            decision=decision,
            violated_constraints=violations,
            details="Violations found" if violations else "All constraints passed",
        )

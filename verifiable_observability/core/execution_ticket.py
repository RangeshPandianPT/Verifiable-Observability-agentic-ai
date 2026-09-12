"""
Signed Execution Tickets — TOCTOU fix for the CCM→dispatch gap.

When the CCM returns ALLOW, it simultaneously issues a signed execution ticket.
The orchestrator must present this ticket to the TicketValidator immediately
before dispatching the action.  The validator checks:

  1. Signature integrity  (HMAC-SHA256)
  2. TTL expiry           (default 30 s)
  3. Action-hash match    (the action hasn't been tampered with since approval)

If any check fails the action MUST NOT be dispatched.

The signing secret is generated per-process by default.  For multi-process
deployments, inject a shared secret via ``TicketIssuer(secret=...)``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

from verifiable_observability.storage.models import Action, ExecutionTicket


# ---------------------------------------------------------------------------
# Canonical hashing
# ---------------------------------------------------------------------------


def canonical_action_hash(
    tool_name: str,
    parameters: dict,
    trajectory_id: str,
    sequence_no: int,
) -> str:
    """
    Compute a deterministic SHA-256 hash over the action's identity.

    The canonical form is:
        tool_name | json(sorted params) | trajectory_id | sequence_no

    Returns:
        Hex-encoded SHA-256 digest.
    """
    canonical = "|".join(
        [
            tool_name,
            json.dumps(parameters, sort_keys=True, default=str),
            trajectory_id,
            str(sequence_no),
        ]
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Issuer
# ---------------------------------------------------------------------------

# Default TTL for execution tickets (seconds).
DEFAULT_TICKET_TTL_SECONDS = 30


class TicketIssuer:
    """
    Issues HMAC-SHA256-signed execution tickets on CCM ALLOW decisions.

    Args:
        secret:      Signing key (bytes).  Defaults to ``os.urandom(32)``.
        ttl_seconds: Ticket time-to-live.  Defaults to 30 s.
    """

    def __init__(
        self,
        secret: bytes | None = None,
        ttl_seconds: int = DEFAULT_TICKET_TTL_SECONDS,
    ) -> None:
        self._secret: bytes = secret or os.urandom(32)
        self._ttl = timedelta(seconds=ttl_seconds)

    @property
    def secret(self) -> bytes:
        """Expose secret for paired TicketValidator."""
        return self._secret

    def issue(
        self,
        action: Action,
        trajectory_id: str,
        sequence_no: int,
    ) -> ExecutionTicket:
        """
        Create a signed ticket for an approved action.

        Args:
            action:        The approved Action.
            trajectory_id: Current trajectory UUID.
            sequence_no:   Monotonic counter within the trajectory.

        Returns:
            ExecutionTicket with a valid signature and TTL.
        """
        now = datetime.now(timezone.utc)
        action_hash = canonical_action_hash(
            action.tool_name, action.parameters, trajectory_id, sequence_no
        )
        payload = f"{action_hash}|{trajectory_id}|{sequence_no}|{now.isoformat()}"
        signature = hmac.new(
            self._secret, payload.encode(), hashlib.sha256
        ).hexdigest()

        return ExecutionTicket(
            action_hash=action_hash,
            trajectory_id=trajectory_id,
            sequence_no=sequence_no,
            issued_at=now,
            expires_at=now + self._ttl,
            signature=signature,
        )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class TicketValidationError(Exception):
    """Raised when a ticket fails validation."""


class TicketValidator:
    """
    Validates execution tickets before action dispatch.

    Must be initialised with the same secret used by the TicketIssuer.
    """

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def validate(
        self,
        ticket: ExecutionTicket,
        action: Action,
        trajectory_id: str,
        sequence_no: int,
    ) -> None:
        """
        Validate a ticket against the action about to be dispatched.

        Raises:
            TicketValidationError: on any validation failure.
        """
        # 1. Check expiry
        now = datetime.now(timezone.utc)
        if now > ticket.expires_at:
            raise TicketValidationError(
                f"Ticket expired at {ticket.expires_at.isoformat()} "
                f"(now={now.isoformat()})"
            )

        # 2. Check action hash
        expected_hash = canonical_action_hash(
            action.tool_name, action.parameters, trajectory_id, sequence_no
        )
        if ticket.action_hash != expected_hash:
            raise TicketValidationError(
                f"Action hash mismatch: ticket={ticket.action_hash[:16]}… "
                f"vs computed={expected_hash[:16]}…  "
                "Action may have been tampered with after approval."
            )

        # 3. Check signature
        payload = (
            f"{ticket.action_hash}|{ticket.trajectory_id}|"
            f"{ticket.sequence_no}|{ticket.issued_at.isoformat()}"
        )
        expected_sig = hmac.new(
            self._secret, payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(ticket.signature, expected_sig):
            raise TicketValidationError("Ticket signature is invalid.")

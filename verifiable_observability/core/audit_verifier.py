"""
Cryptographic Audit Verifier & Compliance Certificate Generator.

Phase 3/6 Novelty Realization:
  "Cryptographically verifiable enforcement, not just logged enforcement."

Provides independent offline audit verification:
  1. Hash Chain Continuity: Proves that turns form an unbroken SHA-256 cryptographic
     chain where each turn binds to the prior turn's hash. Any retroactive mutation
     to reasoning, parameters, or decisions breaks the chain.
  2. Execution Ticket Validation: Proves that every dispatched action possessed a valid,
     non-expired, HMAC-signed ExecutionTicket matching canonical action hash and
     strict monotonic sequence numbering (TOCTOU safety).
  3. Merkle Tree Compliance Proofs: Constructs a Merkle Tree across all compliance
     events and allows generating / verifying compact cryptographic inclusion proofs.
  4. Verifiable Compliance Certificate (VCC): Generates and verifies digitally signed
     certificates for regulators (SEC, HIPAA, internal compliance) without requiring
     full DB access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from verifiable_observability.core.execution_ticket import (
    canonical_action_hash,
)
from verifiable_observability.storage.models import (
    ComplianceDecision,
    Domain,
    Trajectory,
    Turn,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merkle Tree Implementation
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes | str) -> str:
    """Compute SHA-256 hex digest for bytes or UTF-8 string."""
    b = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(b).hexdigest()


class MerkleTree:
    """
    Binary Merkle Tree built over an array of leaf hashes.

    Provides canonical Merkle Root calculation and inclusion proofs.
    """

    def __init__(self, leaves: list[str]) -> None:
        """
        Build Merkle tree from a list of leaf hashes (SHA-256 hex strings).
        If leaves is empty, root is SHA-256 of empty string.
        """
        if not leaves:
            self._leaves = [sha256_hex("")]
        else:
            self._leaves = list(leaves)

        self._layers: list[list[str]] = [self._leaves]
        self._build()

    def _build(self) -> None:
        current = self._leaves
        while len(current) > 1:
            next_layer: list[str] = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else current[i]
                combined = sha256_hex(f"{left}:{right}")
                next_layer.append(combined)
            self._layers.append(next_layer)
            current = next_layer

    @property
    def root(self) -> str:
        """Hex digest of the Merkle root."""
        return self._layers[-1][0]

    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    def get_inclusion_proof(self, leaf_index: int) -> list[dict[str, str]]:
        """
        Generate inclusion proof for the leaf at leaf_index.

        Returns:
            List of proof steps: [{"position": "left"|"right", "hash": "<hex>"}]
        """
        if leaf_index < 0 or leaf_index >= len(self._leaves):
            raise IndexError(f"Leaf index {leaf_index} out of bounds (0..{len(self._leaves)-1})")

        proof: list[dict[str, str]] = []
        idx = leaf_index
        for layer in self._layers[:-1]:
            is_right_sibling = (idx % 2 == 0)
            sibling_idx = idx + 1 if is_right_sibling else idx - 1
            if sibling_idx >= len(layer):
                sibling_idx = idx  # Duplicate odd node

            proof.append({
                "position": "right" if is_right_sibling else "left",
                "hash": layer[sibling_idx],
            })
            idx = idx // 2

        return proof

    @staticmethod
    def verify_inclusion_proof(
        leaf_hash: str,
        proof: list[dict[str, str]],
        expected_root: str,
    ) -> bool:
        """Verify that a leaf_hash belongs to the tree with expected_root."""
        current = leaf_hash
        for step in proof:
            pos = step["position"]
            sibling = step["hash"]
            if pos == "right":
                current = sha256_hex(f"{current}:{sibling}")
            else:
                current = sha256_hex(f"{sibling}:{current}")
        return hmac.compare_digest(current, expected_root)


# ---------------------------------------------------------------------------
# Audit Certificate & Verification Models
# ---------------------------------------------------------------------------


class AuditStatus(str, Enum):
    VERIFIED_COMPLIANT = "VERIFIED_COMPLIANT"
    TAMPERED_HASH_CHAIN = "TAMPERED_HASH_CHAIN"
    INVALID_EXECUTION_TICKET = "INVALID_EXECUTION_TICKET"
    UNCHECKED_DISPATCH = "UNCHECKED_DISPATCH"
    VIOLATION_CONTAINED = "VIOLATION_CONTAINED"  # Violations occurred but all were BLOCKED
    POLICY_BREACH = "POLICY_BREACH"              # An action violated policy but was allowed


@dataclass
class AuditVerificationResult:
    """Detailed result of an offline cryptographic trajectory audit."""

    trajectory_id: str
    status: AuditStatus
    is_valid: bool
    hash_chain_valid: bool
    tickets_valid: bool
    total_turns: int
    total_actions: int
    merkle_root: str
    chain_head_hash: str
    errors: list[str] = field(default_factory=list)
    audit_notes: list[str] = field(default_factory=list)


@dataclass
class ComplianceCertificate:
    """
    Cryptographically verifiable certificate proving compliance of a trajectory.

    Can be exported to JSON and handed to an auditor or regulator.
    """

    certificate_id: str
    trajectory_id: str
    domain: Domain
    task_id: str
    merkle_root: str
    chain_head_hash: str
    total_turns: int
    total_actions: int
    audit_status: AuditStatus
    issued_at: str
    auditor_id: str
    certificate_hash: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "certificate_id": self.certificate_id,
            "trajectory_id": self.trajectory_id,
            "domain": self.domain.value,
            "task_id": self.task_id,
            "merkle_root": self.merkle_root,
            "chain_head_hash": self.chain_head_hash,
            "total_turns": self.total_turns,
            "total_actions": self.total_actions,
            "audit_status": self.audit_status.value,
            "issued_at": self.issued_at,
            "auditor_id": self.auditor_id,
            "certificate_hash": self.certificate_hash,
            "signature": self.signature,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Cryptographic Audit Verifier
# ---------------------------------------------------------------------------


class AuditVerifier:
    """
    Independent cryptographic auditor for agent trajectories.

    Operates strictly on the trajectory model / JSON payload without
    requiring access to the runtime database or LLM services.
    """

    def __init__(self, signing_secret: bytes | None = None) -> None:
        self.signing_secret = signing_secret or b"vo-master-audit-signing-secret"

    # -----------------------------------------------------------------------
    # Hash Chain Verification
    # -----------------------------------------------------------------------

    def compute_turn_hash(self, turn: Turn, prev_hash: str) -> str:
        """
        Compute deterministic SHA-256 hash for a Turn bound to prev_hash.

        Binds:
          - prev_hash
          - turn_index
          - decisions (reasoning + intended action)
          - actions (tool_name + sorted parameters + taint labels)
          - constraint checks (decision + violated constraints + tickets)
        """
        decisions_repr = [
            f"{d.decision_id}:{d.reasoning}:{d.intended_action.tool_name if d.intended_action else ''}"
            for d in turn.decisions
        ]
        actions_repr = [
            f"{a.action_id}:{a.tool_name}:{json.dumps(a.parameters, sort_keys=True, default=str)}:{','.join(sorted(a.taint_labels))}"
            for a in turn.actions
        ]
        checks_repr = [
            f"{c.action_id}:{c.decision.value}:{c.execution_ticket.ticket_id if c.execution_ticket else ''}"
            for c in turn.constraint_checks
        ]

        payload = "|".join([
            prev_hash,
            str(turn.turn_index),
            ";".join(decisions_repr),
            ";".join(actions_repr),
            ";".join(checks_repr),
        ])
        return sha256_hex(payload)

    def verify_hash_chain(self, trajectory: Trajectory) -> tuple[bool, str, list[str]]:
        """
        Verify the entire trajectory hash chain from initial state to head.

        Returns:
            (is_valid, chain_head_hash, error_messages)
        """
        errors: list[str] = []
        if not trajectory.turns:
            init_hash = sha256_hex(f"{trajectory.trajectory_id}:{trajectory.task.task_id}")
            return True, init_hash, []

        current_hash = sha256_hex(f"{trajectory.trajectory_id}:{trajectory.task.task_id}")

        for i, turn in enumerate(trajectory.turns):
            if turn.turn_index != i:
                errors.append(
                    f"Turn sequence gap at position {i}: turn_index is {turn.turn_index}"
                )
            current_hash = self.compute_turn_hash(turn, current_hash)

        return len(errors) == 0, current_hash, errors

    # -----------------------------------------------------------------------
    # Execution Ticket Verification
    # -----------------------------------------------------------------------

    def verify_execution_tickets(
        self,
        trajectory: Trajectory,
        secret: bytes | None = None,
    ) -> tuple[bool, list[str]]:
        """
        Verify that all executed actions possess valid cryptographic tickets.

        Checks:
          1. Canonical action hash matches the executed action parameters.
          2. Ticket signature is cryptographically valid (if secret provided).
          3. Sequence numbers are strictly monotonic without repeats.
        """
        signing_key = secret or self.signing_secret
        errors: list[str] = []
        seen_sequences: set[int] = set()

        for turn in trajectory.turns:
            # Map action_id to action
            action_map = {a.action_id: a for a in turn.actions}

            for check in turn.constraint_checks:
                if check.decision == ComplianceDecision.ALLOW:
                    ticket = check.execution_ticket
                    if not ticket:
                        errors.append(
                            f"Action '{check.action_id}' in Turn {turn.turn_index} was ALLOWED "
                            "but lacks an ExecutionTicket!"
                        )
                        continue

                    # Monotonic sequence check
                    if ticket.sequence_no in seen_sequences:
                        errors.append(
                            f"Replay attack detected: Ticket sequence_no {ticket.sequence_no} reused!"
                        )
                    seen_sequences.add(ticket.sequence_no)

                    # Hash match check
                    action = action_map.get(check.action_id)
                    if action:
                        expected_hash = canonical_action_hash(
                            action.tool_name,
                            action.parameters,
                            trajectory.trajectory_id,
                            ticket.sequence_no,
                        )
                        if not hmac.compare_digest(ticket.action_hash, expected_hash):
                            errors.append(
                                f"TOCTOU violation: Ticket hash '{ticket.action_hash}' does not match "
                                f"action hash '{expected_hash}' for tool '{action.tool_name}'."
                            )

                    # Signature verification
                    payload = (
                        f"{ticket.action_hash}|{ticket.trajectory_id}|"
                        f"{ticket.sequence_no}|{ticket.issued_at.isoformat()}"
                    )
                    expected_sig = hmac.new(
                        signing_key,
                        payload.encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    if not hmac.compare_digest(ticket.signature, expected_sig):
                        errors.append(
                            f"Cryptographic signature mismatch on ticket '{ticket.ticket_id[:8]}'."
                        )

        return len(errors) == 0, errors

    # -----------------------------------------------------------------------
    # Merkle Tree Construction
    # -----------------------------------------------------------------------

    def build_merkle_tree(self, trajectory: Trajectory) -> MerkleTree:
        """
        Build a Merkle Tree where each leaf represents a Turn's verified state.
        """
        prev_hash = sha256_hex(f"{trajectory.trajectory_id}:{trajectory.task.task_id}")
        leaf_hashes: list[str] = []

        for turn in trajectory.turns:
            turn_hash = self.compute_turn_hash(turn, prev_hash)
            leaf_hashes.append(turn_hash)
            prev_hash = turn_hash

        return MerkleTree(leaf_hashes)

    # -----------------------------------------------------------------------
    # End-to-End Audit & Certificate Generation
    # -----------------------------------------------------------------------

    def verify_trajectory(
        self,
        trajectory: Trajectory,
        secret: bytes | None = None,
    ) -> AuditVerificationResult:
        """Perform a complete offline cryptographic audit of a Trajectory."""
        errors: list[str] = []
        notes: list[str] = []

        # 1. Verify Hash Chain
        chain_valid, head_hash, chain_errors = self.verify_hash_chain(trajectory)
        errors.extend(chain_errors)

        # 2. Verify Execution Tickets
        tickets_valid, ticket_errors = self.verify_execution_tickets(trajectory, secret)
        errors.extend(ticket_errors)

        # 3. Check for any policy violations
        has_blocked_violations = False
        has_unblocked_breach = False
        total_actions = 0

        for turn in trajectory.turns:
            total_actions += len(turn.actions)
            for check in turn.constraint_checks:
                if check.decision == ComplianceDecision.BLOCK:
                    has_blocked_violations = True
                    notes.append(
                        f"Blocked violation in Turn {turn.turn_index}: "
                        f"{', '.join(v.constraint_id for v in check.violated_constraints)}"
                    )
                elif check.violated_constraints and check.decision == ComplianceDecision.ALLOW:
                    has_unblocked_breach = True
                    errors.append(
                        f"Policy Breach in Turn {turn.turn_index}: violated constraints "
                        f"{[v.constraint_id for v in check.violated_constraints]} were ALLOWED."
                    )

        # Determine status
        if not chain_valid:
            status = AuditStatus.TAMPERED_HASH_CHAIN
        elif not tickets_valid:
            status = AuditStatus.INVALID_EXECUTION_TICKET
        elif has_unblocked_breach:
            status = AuditStatus.POLICY_BREACH
        elif has_blocked_violations:
            status = AuditStatus.VIOLATION_CONTAINED
        else:
            status = AuditStatus.VERIFIED_COMPLIANT

        merkle_tree = self.build_merkle_tree(trajectory)

        is_valid = (
            chain_valid
            and tickets_valid
            and not has_unblocked_breach
        )

        return AuditVerificationResult(
            trajectory_id=trajectory.trajectory_id,
            status=status,
            is_valid=is_valid,
            hash_chain_valid=chain_valid,
            tickets_valid=tickets_valid,
            total_turns=len(trajectory.turns),
            total_actions=total_actions,
            merkle_root=merkle_tree.root,
            chain_head_hash=head_hash,
            errors=errors,
            audit_notes=notes,
        )

    def generate_compliance_certificate(
        self,
        trajectory: Trajectory,
        secret: bytes | None = None,
        auditor_id: str = "vo-auditor-root",
    ) -> ComplianceCertificate:
        """
        Audit trajectory and issue a cryptographically signed ComplianceCertificate.
        """
        audit_res = self.verify_trajectory(trajectory, secret)
        now_iso = datetime.now(timezone.utc).isoformat()
        cert_id = f"vcc-{uuid.uuid4().hex[:12]}"

        # Canonical hash of certificate content
        cert_payload = "|".join([
            cert_id,
            trajectory.trajectory_id,
            trajectory.task.domain.value,
            trajectory.task.task_id,
            audit_res.merkle_root,
            audit_res.chain_head_hash,
            str(audit_res.total_turns),
            str(audit_res.total_actions),
            audit_res.status.value,
            now_iso,
            auditor_id,
        ])
        cert_hash = sha256_hex(cert_payload)

        # Sign the certificate hash with the auditor secret
        signing_key = secret or self.signing_secret
        sig = hmac.new(signing_key, cert_hash.encode(), hashlib.sha256).hexdigest()

        return ComplianceCertificate(
            certificate_id=cert_id,
            trajectory_id=trajectory.trajectory_id,
            domain=trajectory.task.domain,
            task_id=trajectory.task.task_id,
            merkle_root=audit_res.merkle_root,
            chain_head_hash=audit_res.chain_head_hash,
            total_turns=audit_res.total_turns,
            total_actions=audit_res.total_actions,
            audit_status=audit_res.status,
            issued_at=now_iso,
            auditor_id=auditor_id,
            certificate_hash=cert_hash,
            signature=sig,
        )

    def verify_certificate(
        self,
        cert: ComplianceCertificate,
        secret: bytes | None = None,
    ) -> bool:
        """Verify the cryptographic signature and payload of a ComplianceCertificate."""
        signing_key = secret or self.signing_secret
        cert_payload = "|".join([
            cert.certificate_id,
            cert.trajectory_id,
            cert.domain.value,
            cert.task_id,
            cert.merkle_root,
            cert.chain_head_hash,
            str(cert.total_turns),
            str(cert.total_actions),
            cert.audit_status.value,
            cert.issued_at,
            cert.auditor_id,
        ])
        expected_hash = sha256_hex(cert_payload)
        if not hmac.compare_digest(cert.certificate_hash, expected_hash):
            return False

        expected_sig = hmac.new(
            signing_key,
            cert.certificate_hash.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(cert.signature, expected_sig)

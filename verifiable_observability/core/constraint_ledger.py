"""
Stateful Constraint Ledger — Phase 2a.

An in-memory rolling-window action ledger (simulated Redis) that enables
velocity checks.  The FinanceCCM uses this to detect structuring attacks
where an agent breaks a large transfer into many small ones to bypass a
per-transaction limit.

Example::

    ledger = ConstraintLedger()
    ledger.record_action("traj-1", action, amount=4999.0)
    report = ledger.get_velocity("traj-1", "execute_transfer", window_seconds=3600)
    if report.total_amount > 50_000:
        # BLOCK
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Velocity report
# ---------------------------------------------------------------------------


@dataclass
class VelocityReport:
    """Aggregated velocity data for a tool within a time window."""

    tool_name: str
    count: int = 0
    total_amount: float = 0.0
    window_seconds: int = 3600


# ---------------------------------------------------------------------------
# Ledger entry
# ---------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    """One recorded action in the ledger."""

    trajectory_id: str
    tool_name: str
    amount: float
    timestamp: float  # Unix epoch seconds


# ---------------------------------------------------------------------------
# Constraint Ledger
# ---------------------------------------------------------------------------

# Default rolling window for velocity checks (1 hour).
DEFAULT_WINDOW_SECONDS = 3600

# Default velocity thresholds for the Finance domain.
FINANCE_VELOCITY_MAX_AMOUNT = 50_000.0
FINANCE_VELOCITY_MAX_COUNT = 10


class ConstraintLedger:
    """
    In-memory, thread-safe rolling-window action ledger.

    Records actions with their amounts and provides velocity queries
    over configurable time windows.
    """

    def __init__(self, default_window_seconds: int = DEFAULT_WINDOW_SECONDS) -> None:
        self._entries: list[LedgerEntry] = []
        self._lock = threading.Lock()
        self._default_window = default_window_seconds

    def record_action(
        self,
        trajectory_id: str,
        tool_name: str,
        amount: float = 0.0,
        timestamp: float | None = None,
    ) -> None:
        """
        Record an action in the ledger.

        Args:
            trajectory_id: The trajectory that produced this action.
            tool_name:     The tool that was called.
            amount:        Monetary amount (0.0 for non-financial tools).
            timestamp:     Unix epoch; defaults to ``time.time()``.
        """
        ts = timestamp if timestamp is not None else time.time()
        entry = LedgerEntry(
            trajectory_id=trajectory_id,
            tool_name=tool_name,
            amount=amount,
            timestamp=ts,
        )
        with self._lock:
            self._entries.append(entry)

    def get_velocity(
        self,
        trajectory_id: str | None = None,
        tool_name: str | None = None,
        window_seconds: int | None = None,
    ) -> VelocityReport:
        """
        Query the ledger for action velocity within a rolling window.

        Args:
            trajectory_id: Filter by trajectory (None = all trajectories).
            tool_name:     Filter by tool name (None = all tools).
            window_seconds: Window size; defaults to ``self._default_window``.

        Returns:
            VelocityReport with count and total_amount within the window.
        """
        window = window_seconds or self._default_window
        cutoff = time.time() - window

        with self._lock:
            matching = [
                e
                for e in self._entries
                if e.timestamp >= cutoff
                and (trajectory_id is None or e.trajectory_id == trajectory_id)
                and (tool_name is None or e.tool_name == tool_name)
            ]

        return VelocityReport(
            tool_name=tool_name or "*",
            count=len(matching),
            total_amount=sum(e.amount for e in matching),
            window_seconds=window,
        )

    def clear(self) -> None:
        """Remove all entries (useful for tests)."""
        with self._lock:
            self._entries.clear()

    def prune(self, older_than_seconds: int | None = None) -> int:
        """
        Remove entries older than the given window.

        Returns the number of pruned entries.
        """
        cutoff = time.time() - (older_than_seconds or self._default_window)
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.timestamp >= cutoff]
            return before - len(self._entries)

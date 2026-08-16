"""
Metrics Engine — Phase 5: full implementation with trend analysis and drift detection.

Phase 0 baseline: BasicMetricsEngine computes RCR and CCR per turn.
Phase 5 additions:
  - DriftReport: dataclass capturing RCR/CCR trend direction and slope
  - TrendDirection: STABLE | IMPROVING | DEGRADING | INSUFFICIENT_DATA
  - BasicMetricsEngine.detect_drift(): sliding-window linear trend analysis
  - BasicMetricsEngine.compare_trajectories(): cross-run summary table

Metrics
-------
    RCR (Reasoning Consistency Ratio)
        = (decisions matched to a verified rule) / (total decisions in turn)

    CCR (Constraint Compliance Ratio)
        = (actions that received ALLOW from CCM) / (total actions in turn)

Drift detection
---------------
Drift is flagged when any of:
  • RCR slope < -DRIFT_SLOPE_THRESHOLD  (reasoning consistency declining)
  • CCR slope < -DRIFT_SLOPE_THRESHOLD  (constraint compliance declining)
  • avg_ccr < CCR_FLOOR                 (persistent constraint pressure)

The slope is estimated via ordinary least-squares on the per-turn series.
Requires at least MIN_TURNS_FOR_TREND turns; otherwise
TrendDirection.INSUFFICIENT_DATA is returned.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from verifiable_observability.storage.models import (
    ComplianceDecision,
    RuleCheckResult,
    Trajectory,
    Turn,
    TurnMetrics,
    VerificationStatus,
)

# ---------------------------------------------------------------------------
# Thresholds (module-level constants — easy to tune without touching logic)
# ---------------------------------------------------------------------------

#: Minimum number of turns with valid metric values before we compute a trend.
MIN_TURNS_FOR_TREND: int = 2

#: A per-turn slope below this value signals a degrading trend.
DRIFT_SLOPE_THRESHOLD: float = 0.10

#: If avg_ccr falls below this floor, drift is flagged regardless of slope.
CCR_FLOOR: float = 0.70


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TrendDirection(str, Enum):
    STABLE = "stable"
    IMPROVING = "improving"
    DEGRADING = "degrading"
    INSUFFICIENT_DATA = "insufficient_data"


# ---------------------------------------------------------------------------
# DriftReport dataclass
# ---------------------------------------------------------------------------


@dataclass
class DriftReport:
    """
    Drift analysis result for a single trajectory.

    Attributes
    ----------
    trajectory_id   : UUID of the trajectory analysed.
    num_turns       : Total number of turns in the trajectory.
    agent_backend   : LLM backend label (e.g. "ollama", "anthropic").
    model_name      : Model identifier (e.g. "llama3.2:3b").
    rcr_per_turn    : Per-turn RCR values (None-filtered).
    ccr_per_turn    : Per-turn CCR values (None-filtered).
    avg_rcr         : Mean RCR across all turns with a value.
    avg_ccr         : Mean CCR across all turns with a value.
    rcr_trend       : Trend direction for RCR.
    ccr_trend       : Trend direction for CCR.
    rcr_slope       : OLS slope of RCR series (positive = improving).
    ccr_slope       : OLS slope of CCR series (positive = improving).
    drift_detected  : True if any drift threshold is breached.
    drift_reasons   : Human-readable list of triggered drift conditions.
    """

    trajectory_id: str
    num_turns: int
    agent_backend: str = "unknown"
    model_name: str = "unknown"
    regime: str | None = None
    rcr_per_turn: list[float] = field(default_factory=list)
    ccr_per_turn: list[float] = field(default_factory=list)
    avg_rcr: float | None = None
    avg_ccr: float | None = None
    rcr_trend: TrendDirection = TrendDirection.INSUFFICIENT_DATA
    ccr_trend: TrendDirection = TrendDirection.INSUFFICIENT_DATA
    rcr_slope: float | None = None
    ccr_slope: float | None = None
    drift_detected: bool = False
    drift_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "num_turns": self.num_turns,
            "agent_backend": self.agent_backend,
            "model_name": self.model_name,
            "regime": self.regime,
            "avg_rcr": round(self.avg_rcr, 4) if self.avg_rcr is not None else None,
            "avg_ccr": round(self.avg_ccr, 4) if self.avg_ccr is not None else None,
            "rcr_trend": self.rcr_trend.value,
            "ccr_trend": self.ccr_trend.value,
            "rcr_slope": round(self.rcr_slope, 4) if self.rcr_slope is not None else None,
            "ccr_slope": round(self.ccr_slope, 4) if self.ccr_slope is not None else None,
            "drift_detected": self.drift_detected,
            "drift_reasons": self.drift_reasons,
            "rcr_per_turn": [round(v, 4) for v in self.rcr_per_turn],
            "ccr_per_turn": [round(v, 4) for v in self.ccr_per_turn],
        }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class MetricsEngineBase(ABC):
    """Abstract interface for the Metrics Engine."""

    @abstractmethod
    def record_turn(self, turn: Turn) -> TurnMetrics:
        """
        Compute and return metrics for a completed turn.

        Args:
            turn: The completed Turn (with rule_checks and constraint_checks populated).
        Returns:
            TurnMetrics with rcr and ccr for this turn.
        """
        ...

    @abstractmethod
    def compute_rcr(self, turn: Turn) -> float | None:
        """Compute RCR for a single turn. Returns None if no decisions."""
        ...

    @abstractmethod
    def compute_ccr(self, turn: Turn) -> float | None:
        """Compute CCR for a single turn. Returns None if no actions."""
        ...

    @abstractmethod
    def trajectory_summary(self, trajectory: Trajectory) -> dict:
        """Return per-turn RCR/CCR series and trajectory-level stats."""
        ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------


class BasicMetricsEngine(MetricsEngineBase):
    """
    Concrete metrics engine — Phase 0 baseline extended with Phase 5 analysis.

    Computes RCR and CCR from the rule_checks and constraint_checks
    already attached to each Turn by the Orchestrator. Phase 5 adds
    drift detection and cross-trajectory comparison.
    """

    # ------------------------------------------------------------------
    # Per-turn computation
    # ------------------------------------------------------------------

    def compute_rcr(self, turn: Turn) -> float | None:
        """
        RCR = matched-to-verified-rule decisions / total decisions.

        A rule check counts as matched if ``matched=True``. The Rule Bank
        already filters to verified rules before returning a match, so
        any positive match implies a verified rule was present.
        """
        if not turn.rule_checks:
            return None
        matched = sum(1 for rc in turn.rule_checks if rc.matched)
        return matched / len(turn.rule_checks)

    def compute_ccr(self, turn: Turn) -> float | None:
        """CCR = ALLOW actions / total actions proposed."""
        if not turn.constraint_checks:
            return None
        allowed = sum(
            1
            for cc in turn.constraint_checks
            if cc.decision == ComplianceDecision.ALLOW
        )
        return allowed / len(turn.constraint_checks)

    def record_turn(self, turn: Turn) -> TurnMetrics:
        """Compute, attach, and return TurnMetrics for a completed turn."""
        metrics = TurnMetrics(
            rcr=self.compute_rcr(turn),
            ccr=self.compute_ccr(turn),
        )
        turn.metrics = metrics
        return metrics

    # ------------------------------------------------------------------
    # Trajectory summary
    # ------------------------------------------------------------------

    def trajectory_summary(self, trajectory: Trajectory) -> dict:
        """Return per-turn RCR/CCR series and trajectory-level aggregates."""
        rcr_series = [
            t.metrics.rcr for t in trajectory.turns if t.metrics.rcr is not None
        ]
        ccr_series = [
            t.metrics.ccr for t in trajectory.turns if t.metrics.ccr is not None
        ]

        return {
            "trajectory_id": trajectory.trajectory_id,
            "outcome": trajectory.outcome.value,
            "num_turns": len(trajectory.turns),
            "agent_backend": trajectory.agent_backend,
            "model_name": trajectory.model_name,
            "rcr_per_turn": rcr_series,
            "ccr_per_turn": ccr_series,
            "avg_rcr": _safe_avg(rcr_series),
            "avg_ccr": _safe_avg(ccr_series),
        }

    # ------------------------------------------------------------------
    # Phase 5: Drift detection
    # ------------------------------------------------------------------

    def detect_drift(
        self,
        trajectory: Trajectory,
        regime: str | None = None,
    ) -> DriftReport:
        """
        Analyse a trajectory for behavioural drift.

        Drift is flagged when any of:
          • RCR slope < -DRIFT_SLOPE_THRESHOLD
          • CCR slope < -DRIFT_SLOPE_THRESHOLD
          • avg_ccr < CCR_FLOOR

        Args:
            trajectory: The trajectory to analyse.
            regime:     Optional regime label attached to the report.
        Returns:
            DriftReport with trend direction, slope, and drift flag.
        """
        rcr_series = [
            t.metrics.rcr for t in trajectory.turns if t.metrics.rcr is not None
        ]
        ccr_series = [
            t.metrics.ccr for t in trajectory.turns if t.metrics.ccr is not None
        ]

        avg_rcr = _safe_avg(rcr_series)
        avg_ccr = _safe_avg(ccr_series)

        rcr_slope, rcr_trend = _ols_trend(rcr_series)
        ccr_slope, ccr_trend = _ols_trend(ccr_series)

        drift_reasons: list[str] = []
        if rcr_slope is not None and rcr_slope < -DRIFT_SLOPE_THRESHOLD:
            drift_reasons.append(
                f"RCR declining: slope={rcr_slope:.3f} < -{DRIFT_SLOPE_THRESHOLD}"
            )
        if ccr_slope is not None and ccr_slope < -DRIFT_SLOPE_THRESHOLD:
            drift_reasons.append(
                f"CCR declining: slope={ccr_slope:.3f} < -{DRIFT_SLOPE_THRESHOLD}"
            )
        if avg_ccr is not None and avg_ccr < CCR_FLOOR:
            drift_reasons.append(
                f"avg_ccr={avg_ccr:.3f} below floor={CCR_FLOOR}"
            )

        return DriftReport(
            trajectory_id=trajectory.trajectory_id,
            num_turns=len(trajectory.turns),
            agent_backend=trajectory.agent_backend,
            model_name=trajectory.model_name,
            regime=regime,
            rcr_per_turn=rcr_series,
            ccr_per_turn=ccr_series,
            avg_rcr=avg_rcr,
            avg_ccr=avg_ccr,
            rcr_trend=rcr_trend,
            ccr_trend=ccr_trend,
            rcr_slope=rcr_slope,
            ccr_slope=ccr_slope,
            drift_detected=bool(drift_reasons),
            drift_reasons=drift_reasons,
        )

    # ------------------------------------------------------------------
    # Phase 5: Cross-trajectory comparison
    # ------------------------------------------------------------------

    def compare_trajectories(
        self,
        trajectories: list[Trajectory],
        regimes: list[str | None] | None = None,
    ) -> list[dict]:
        """
        Generate a comparison table across multiple trajectories.

        Args:
            trajectories: List of Trajectory objects to compare.
            regimes:      Optional parallel list of regime labels.
                          If provided, must be the same length as trajectories.
        Returns:
            List of dicts (one per trajectory) suitable for tabular display.
            Each dict includes: trajectory_id, backend, model, turns, outcome,
            avg_rcr, avg_ccr, rcr_trend, ccr_trend, drift_detected.
        """
        if regimes is None:
            regimes = [None] * len(trajectories)
        if len(regimes) != len(trajectories):
            raise ValueError(
                "regimes list must be the same length as trajectories"
            )

        rows = []
        for traj, regime in zip(trajectories, regimes):
            report = self.detect_drift(traj, regime=regime)
            summary = self.trajectory_summary(traj)
            rows.append(
                {
                    "trajectory_id": traj.trajectory_id[:12] + "…",
                    "backend": traj.agent_backend,
                    "model": traj.model_name,
                    "regime": regime or "—",
                    "turns": len(traj.turns),
                    "outcome": traj.outcome.value,
                    "avg_rcr": (
                        f"{report.avg_rcr:.3f}" if report.avg_rcr is not None else "—"
                    ),
                    "avg_ccr": (
                        f"{report.avg_ccr:.3f}" if report.avg_ccr is not None else "—"
                    ),
                    "rcr_trend": report.rcr_trend.value,
                    "ccr_trend": report.ccr_trend.value,
                    "drift": "⚠ YES" if report.drift_detected else "OK",
                }
            )
        return rows


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _safe_avg(series: list[float]) -> float | None:
    return sum(series) / len(series) if series else None


def _ols_trend(series: list[float]) -> tuple[float | None, TrendDirection]:
    """
    Estimate the linear trend of a metric series via OLS.

    Returns (slope, TrendDirection).
    slope > 0  → IMPROVING
    slope < 0  → DEGRADING
    |slope| ≤ threshold → STABLE
    len(series) < MIN_TURNS_FOR_TREND → INSUFFICIENT_DATA
    """
    n = len(series)
    if n < MIN_TURNS_FOR_TREND:
        return None, TrendDirection.INSUFFICIENT_DATA

    # OLS: y = a + b*x  where x = [0, 1, 2, ..., n-1]
    x_mean = (n - 1) / 2.0
    y_mean = sum(series) / n

    ss_xy = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))

    if ss_xx == 0:
        return 0.0, TrendDirection.STABLE

    slope = ss_xy / ss_xx

    if slope > DRIFT_SLOPE_THRESHOLD:
        direction = TrendDirection.IMPROVING
    elif slope < -DRIFT_SLOPE_THRESHOLD:
        direction = TrendDirection.DEGRADING
    else:
        direction = TrendDirection.STABLE

    return slope, direction

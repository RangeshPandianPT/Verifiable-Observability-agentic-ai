"""
Metrics Engine — Phase 5 baseline + Phase 3 v2 upgrades.

Phase 0 baseline: BasicMetricsEngine computes RCR and CCR per turn.
Phase 5 additions:
  - DriftReport: dataclass capturing RCR/CCR trend direction and slope
  - TrendDirection: STABLE | IMPROVING | DEGRADING | INSUFFICIENT_DATA
  - BasicMetricsEngine.detect_drift(): sliding-window linear trend analysis
  - BasicMetricsEngine.compare_trajectories(): cross-run summary table

Phase 3 v2 additions:
  - BetaBinomialModel: Bayesian modeling of turn outcomes
  - CUSUMDetector: Cumulative Sum changepoint detection
  - Drift attribution by rule-ID and tool-ID
  - MetricsEngineV2: extends BasicMetricsEngine with v2 capabilities

Metrics
-------
    RCR (Reasoning Consistency Ratio)
        = (decisions matched to a verified rule) / (total decisions in turn)

    CCR (Constraint Compliance Ratio)
        = (actions that received ALLOW from CCM) / (total actions in turn)

Drift detection (Phase 5 — OLS, retained for backward compatibility)
---------------
Drift is flagged when any of:
  • RCR slope < -DRIFT_SLOPE_THRESHOLD  (reasoning consistency declining)
  • CCR slope < -DRIFT_SLOPE_THRESHOLD  (constraint compliance declining)
  • avg_ccr < CCR_FLOOR                 (persistent constraint pressure)

Changepoint detection (Phase 3 — CUSUM)
---------------------
A more robust approach using Cumulative Sum (CUSUM) to detect abrupt
shifts in the compliance time series, replacing the OLS slope as the
primary drift signal.
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
# Beta-Binomial Model (Phase 3a)
# ---------------------------------------------------------------------------


@dataclass
class BetaBinomialModel:
    """
    Bayesian Beta-Binomial model for turn-level compliance outcomes.

    Starts with a uniform prior (alpha=1, beta=1).  Each batch of turn
    outcomes updates the posterior via conjugate updating.

    Attributes:
        alpha: Success count + prior.
        beta:  Failure count + prior.
    """

    alpha: float = 1.0
    beta: float = 1.0

    def update(self, successes: int, trials: int) -> None:
        """
        Bayesian update: alpha += successes, beta += failures.

        Args:
            successes: Number of compliant outcomes in this batch.
            trials:    Total outcomes in this batch.
        """
        if trials < 0 or successes < 0 or successes > trials:
            raise ValueError(
                f"Invalid update: successes={successes}, trials={trials}"
            )
        self.alpha += successes
        self.beta += (trials - successes)

    @property
    def mean(self) -> float:
        """Posterior mean: alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        """Posterior variance of the Beta distribution."""
        a, b = self.alpha, self.beta
        return (a * b) / ((a + b) ** 2 * (a + b + 1))

    def credible_interval(self, level: float = 0.95) -> tuple[float, float]:
        """
        Compute a symmetric credible interval for the posterior.

        Uses scipy.stats.beta.ppf for the quantile function.

        Args:
            level: Credible level (e.g. 0.95 for 95% CI).

        Returns:
            (lower, upper) bounds.
        """
        try:
            from scipy.stats import beta as beta_dist

            tail = (1.0 - level) / 2.0
            lower = float(beta_dist.ppf(tail, self.alpha, self.beta))
            upper = float(beta_dist.ppf(1.0 - tail, self.alpha, self.beta))
            return (lower, upper)
        except ImportError:
            # Fallback: use normal approximation
            import math

            std = math.sqrt(self.variance)
            z = 1.96 if level >= 0.95 else 1.645  # rough z-scores
            return (
                max(0.0, self.mean - z * std),
                min(1.0, self.mean + z * std),
            )

    def reset(self) -> None:
        """Reset to uniform prior."""
        self.alpha = 1.0
        self.beta = 1.0


# ---------------------------------------------------------------------------
# CUSUM Changepoint Detector (Phase 3b)
# ---------------------------------------------------------------------------


@dataclass
class ChangePoint:
    """A detected changepoint in a time series."""

    index: int
    direction: str  # "up" or "down"
    cumulative_sum: float


class CUSUMDetector:
    """
    Cumulative Sum (CUSUM) changepoint detector.

    Detects abrupt shifts in a time series away from a target mean.
    Uses a two-sided CUSUM: tracks both upward and downward shifts.

    Args:
        threshold:       Decision threshold (h).  A changepoint is flagged
                         when the cumulative sum exceeds this value.
        drift_magnitude: Expected shift size (k / slack / allowance).
                         Typically set to half the expected shift magnitude.
    """

    def __init__(
        self,
        threshold: float = 2.0,
        drift_magnitude: float = 0.1,
    ) -> None:
        self.threshold = threshold
        self.drift_magnitude = drift_magnitude

    def detect(
        self,
        series: list[float],
        target_mean: float | None = None,
    ) -> list[ChangePoint]:
        """
        Run two-sided CUSUM on a series.

        Args:
            series:      The time series to analyse.
            target_mean: The "in-control" mean.  Defaults to the overall mean.

        Returns:
            List of ChangePoint objects where the CUSUM exceeded the threshold.
        """
        if len(series) < 2:
            return []

        if target_mean is None:
            target_mean = sum(series) / len(series)

        s_high = 0.0  # cumulative sum for upward shift
        s_low = 0.0   # cumulative sum for downward shift
        changepoints: list[ChangePoint] = []

        for i, value in enumerate(series):
            s_high = max(0.0, s_high + (value - target_mean) - self.drift_magnitude)
            s_low = max(0.0, s_low - (value - target_mean) - self.drift_magnitude)

            if s_high > self.threshold:
                changepoints.append(
                    ChangePoint(index=i, direction="up", cumulative_sum=s_high)
                )
                s_high = 0.0  # reset after detection

            if s_low > self.threshold:
                changepoints.append(
                    ChangePoint(index=i, direction="down", cumulative_sum=s_low)
                )
                s_low = 0.0  # reset after detection

        return changepoints


# ---------------------------------------------------------------------------
# Drift Attribution (Phase 3c)
# ---------------------------------------------------------------------------


@dataclass
class DriftAttribution:
    """Per-rule and per-tool compliance attribution for a trajectory."""

    rule_compliance: dict[str, float] = field(default_factory=dict)
    tool_compliance: dict[str, float] = field(default_factory=dict)
    degrading_rules: list[str] = field(default_factory=list)
    degrading_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_compliance": self.rule_compliance,
            "tool_compliance": self.tool_compliance,
            "degrading_rules": self.degrading_rules,
            "degrading_tools": self.degrading_tools,
        }


# ---------------------------------------------------------------------------
# DriftReport dataclass (extended for Phase 3)
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
    # --- Phase 3 extensions ---
    rcr_posterior_mean   : Beta-Binomial posterior mean for RCR.
    ccr_posterior_mean   : Beta-Binomial posterior mean for CCR.
    rcr_credible_interval: 95% credible interval for RCR.
    ccr_credible_interval: 95% credible interval for CCR.
    rcr_changepoints     : CUSUM-detected changepoints in RCR series.
    ccr_changepoints     : CUSUM-detected changepoints in CCR series.
    attribution          : Per-rule/tool drift attribution.
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
    # Phase 3 extensions
    rcr_posterior_mean: float | None = None
    ccr_posterior_mean: float | None = None
    rcr_credible_interval: tuple[float, float] | None = None
    ccr_credible_interval: tuple[float, float] | None = None
    rcr_changepoints: list[ChangePoint] = field(default_factory=list)
    ccr_changepoints: list[ChangePoint] = field(default_factory=list)
    attribution: DriftAttribution | None = None

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
            # Phase 3
            "rcr_posterior_mean": (
                round(self.rcr_posterior_mean, 4)
                if self.rcr_posterior_mean is not None
                else None
            ),
            "ccr_posterior_mean": (
                round(self.ccr_posterior_mean, 4)
                if self.ccr_posterior_mean is not None
                else None
            ),
            "rcr_credible_interval": (
                tuple(round(v, 4) for v in self.rcr_credible_interval)
                if self.rcr_credible_interval
                else None
            ),
            "ccr_credible_interval": (
                tuple(round(v, 4) for v in self.ccr_credible_interval)
                if self.ccr_credible_interval
                else None
            ),
            "rcr_changepoints": [
                {"index": cp.index, "direction": cp.direction}
                for cp in self.rcr_changepoints
            ],
            "ccr_changepoints": [
                {"index": cp.index, "direction": cp.direction}
                for cp in self.ccr_changepoints
            ],
            "attribution": self.attribution.to_dict() if self.attribution else None,
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
# Concrete implementation (Phase 0 baseline + Phase 5 + Phase 3 v2)
# ---------------------------------------------------------------------------


class BasicMetricsEngine(MetricsEngineBase):
    """
    Concrete metrics engine — Phase 0 baseline extended with Phase 5 analysis
    and Phase 3 v2 upgrades.

    Computes RCR and CCR from the rule_checks and constraint_checks
    already attached to each Turn by the Orchestrator. Phase 5 adds
    drift detection and cross-trajectory comparison. Phase 3 adds
    Beta-Binomial modeling, CUSUM changepoint detection, and drift
    attribution.
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
    # Phase 5: Drift detection (OLS — retained for backward compatibility)
    # ------------------------------------------------------------------

    def detect_drift(
        self,
        trajectory: Trajectory,
        regime: str | None = None,
    ) -> DriftReport:
        """
        Analyse a trajectory for behavioural drift.

        Uses both OLS trend (Phase 5) and CUSUM changepoint detection (Phase 3).
        Also computes Beta-Binomial posteriors and drift attribution.

        Args:
            trajectory: The trajectory to analyse.
            regime:     Optional regime label attached to the report.
        Returns:
            DriftReport with trend direction, slope, changepoints, and drift flag.
        """
        rcr_series = [
            t.metrics.rcr for t in trajectory.turns if t.metrics.rcr is not None
        ]
        ccr_series = [
            t.metrics.ccr for t in trajectory.turns if t.metrics.ccr is not None
        ]

        avg_rcr = _safe_avg(rcr_series)
        avg_ccr = _safe_avg(ccr_series)

        # Phase 5: OLS trend
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

        # Phase 3a: Beta-Binomial posteriors
        rcr_model = BetaBinomialModel()
        ccr_model = BetaBinomialModel()
        for turn in trajectory.turns:
            if turn.rule_checks:
                matched = sum(1 for rc in turn.rule_checks if rc.matched)
                rcr_model.update(matched, len(turn.rule_checks))
            if turn.constraint_checks:
                allowed = sum(
                    1
                    for cc in turn.constraint_checks
                    if cc.decision == ComplianceDecision.ALLOW
                )
                ccr_model.update(allowed, len(turn.constraint_checks))

        rcr_posterior_mean = rcr_model.mean if rcr_model.alpha + rcr_model.beta > 2 else None
        ccr_posterior_mean = ccr_model.mean if ccr_model.alpha + ccr_model.beta > 2 else None
        rcr_ci = rcr_model.credible_interval() if rcr_posterior_mean is not None else None
        ccr_ci = ccr_model.credible_interval() if ccr_posterior_mean is not None else None

        # Phase 3b: CUSUM changepoint detection
        cusum = CUSUMDetector(threshold=1.5, drift_magnitude=0.05)
        rcr_changepoints = cusum.detect(rcr_series) if len(rcr_series) >= 2 else []
        ccr_changepoints = cusum.detect(ccr_series) if len(ccr_series) >= 2 else []

        # CUSUM changepoints as drift signals
        if ccr_changepoints:
            down_cps = [cp for cp in ccr_changepoints if cp.direction == "down"]
            if down_cps:
                drift_reasons.append(
                    f"CUSUM: {len(down_cps)} downward CCR changepoint(s) "
                    f"at indices {[cp.index for cp in down_cps]}"
                )

        # Phase 3c: Drift attribution
        attribution = self.attribute_drift(trajectory)

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
            rcr_posterior_mean=rcr_posterior_mean,
            ccr_posterior_mean=ccr_posterior_mean,
            rcr_credible_interval=rcr_ci,
            ccr_credible_interval=ccr_ci,
            rcr_changepoints=rcr_changepoints,
            ccr_changepoints=ccr_changepoints,
            attribution=attribution,
        )

    # ------------------------------------------------------------------
    # Phase 3c: Drift attribution
    # ------------------------------------------------------------------

    def attribute_drift(
        self,
        trajectory: Trajectory,
        compliance_threshold: float = 0.80,
    ) -> DriftAttribution:
        """
        Decompose drift by rule-ID and tool-ID.

        Groups constraint checks by the rule_id from rule_checks and by
        tool_name from actions, computing per-group compliance rates.

        Args:
            trajectory:           The trajectory to analyse.
            compliance_threshold: Groups below this threshold are flagged
                                  as degrading.

        Returns:
            DriftAttribution with per-rule and per-tool compliance rates.
        """
        # Per-rule compliance: for each rule_id, how often was it matched?
        rule_totals: dict[str, int] = {}
        rule_matches: dict[str, int] = {}
        for turn in trajectory.turns:
            for rc in turn.rule_checks:
                if rc.rule_id:
                    rule_totals[rc.rule_id] = rule_totals.get(rc.rule_id, 0) + 1
                    if rc.matched:
                        rule_matches[rc.rule_id] = rule_matches.get(rc.rule_id, 0) + 1

        rule_compliance = {
            rid: rule_matches.get(rid, 0) / total
            for rid, total in rule_totals.items()
            if total > 0
        }

        # Per-tool compliance: for each tool_name, how often was it ALLOW'd?
        tool_totals: dict[str, int] = {}
        tool_allows: dict[str, int] = {}
        for turn in trajectory.turns:
            for cc in turn.constraint_checks:
                # Find the corresponding tool name from actions
                tool_name = None
                for act in turn.actions:
                    if act.action_id == cc.action_id:
                        tool_name = act.tool_name
                        break
                if tool_name is None and turn.actions:
                    tool_name = turn.actions[0].tool_name
                if tool_name:
                    tool_totals[tool_name] = tool_totals.get(tool_name, 0) + 1
                    if cc.decision == ComplianceDecision.ALLOW:
                        tool_allows[tool_name] = tool_allows.get(tool_name, 0) + 1

        tool_compliance = {
            tn: tool_allows.get(tn, 0) / total
            for tn, total in tool_totals.items()
            if total > 0
        }

        degrading_rules = [
            rid for rid, rate in rule_compliance.items()
            if rate < compliance_threshold
        ]
        degrading_tools = [
            tn for tn, rate in tool_compliance.items()
            if rate < compliance_threshold
        ]

        return DriftAttribution(
            rule_compliance=rule_compliance,
            tool_compliance=tool_compliance,
            degrading_rules=degrading_rules,
            degrading_tools=degrading_tools,
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
                    "failure_reason": traj.failure_reason,
                }
            )
        return rows


# Backward compatibility alias
MetricsEngineV2 = BasicMetricsEngine


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

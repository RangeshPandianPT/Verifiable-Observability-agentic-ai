"""
Phase 3 tests — Metrics Engine v2.

Covers:
  3a. Beta-Binomial posterior: correct mean, credible intervals.
  3b. CUSUM changepoint detection: synthetic drift series.
  3c. Drift attribution by rule-ID and tool-ID.
  3d. Backward compatibility: BasicMetricsEngine alias, _ols_trend.
"""

from __future__ import annotations

import pytest

from verifiable_observability.core.metrics import (
    BasicMetricsEngine,
    BetaBinomialModel,
    CCR_FLOOR,
    ChangePoint,
    CUSUMDetector,
    DriftAttribution,
    DriftReport,
    MetricsEngineV2,
    TrendDirection,
    _ols_trend,
    _safe_avg,
)
from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    ConstraintCheckResult,
    Decision,
    Domain,
    RuleCheckResult,
    Task,
    Trajectory,
    Turn,
    TurnMetrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trajectory_with_turns(
    rcr_values: list[float],
    ccr_values: list[float],
    rule_ids: list[str | None] | None = None,
    tool_names: list[str | None] | None = None,
) -> Trajectory:
    """Build a Trajectory with synthetic turn data."""
    task = Task(domain=Domain.FINANCE, description="Test trajectory")
    traj = Trajectory(task=task, agent_backend="test", model_name="test")

    for i, (rcr, ccr) in enumerate(zip(rcr_values, ccr_values)):
        turn = Turn(turn_index=i)

        # Create rule checks based on rcr
        rule_id = rule_ids[i] if rule_ids and i < len(rule_ids) else f"rule-{i}"
        if rcr >= 0.5:
            turn.rule_checks.append(
                RuleCheckResult(
                    decision_id=f"dec-{i}",
                    matched=True,
                    rule_id=rule_id,
                    rule_name=f"test_rule_{i}",
                    confidence=rcr,
                    match_method="structured_predicate",
                )
            )
        else:
            turn.rule_checks.append(
                RuleCheckResult(
                    decision_id=f"dec-{i}",
                    matched=False,
                    rule_id=rule_id,
                    confidence=rcr,
                    match_method="none",
                )
            )

        # Create constraint checks based on ccr
        tool_name = tool_names[i] if tool_names and i < len(tool_names) else f"tool_{i}"
        action = Action(tool_name=tool_name, parameters={})
        turn.actions.append(action)
        cc_decision = ComplianceDecision.ALLOW if ccr >= 0.5 else ComplianceDecision.BLOCK
        turn.constraint_checks.append(
            ConstraintCheckResult(
                action_id=action.action_id,
                decision=cc_decision,
                details="test",
            )
        )

        turn.metrics = TurnMetrics(rcr=rcr, ccr=ccr)
        traj.turns.append(turn)

    return traj


# ---------------------------------------------------------------------------
# 3a. Beta-Binomial Model
# ---------------------------------------------------------------------------


class TestBetaBinomialModel:
    """Beta-Binomial posterior computation."""

    def test_uniform_prior(self):
        model = BetaBinomialModel()
        assert model.alpha == 1.0
        assert model.beta == 1.0
        assert model.mean == 0.5

    def test_update_shifts_posterior(self):
        model = BetaBinomialModel()
        # 8 out of 10 successes → posterior alpha=9, beta=3
        model.update(successes=8, trials=10)
        assert model.alpha == 9.0
        assert model.beta == 3.0
        # Posterior mean ≈ 0.75
        assert abs(model.mean - 0.75) < 0.01

    def test_multiple_updates(self):
        model = BetaBinomialModel()
        model.update(5, 5)   # alpha=6, beta=1
        model.update(3, 5)   # alpha=9, beta=3
        assert model.alpha == 9.0
        assert model.beta == 3.0

    def test_credible_interval_contains_mean(self):
        model = BetaBinomialModel()
        model.update(80, 100)
        ci = model.credible_interval(0.95)
        assert ci[0] <= model.mean <= ci[1]
        # The CI should be reasonably narrow with 100 observations
        assert ci[1] - ci[0] < 0.20

    def test_credible_interval_wide_with_few_observations(self):
        model = BetaBinomialModel()
        model.update(2, 3)
        ci = model.credible_interval(0.95)
        assert ci[0] <= model.mean <= ci[1]
        # With only 3 observations, the interval should be wide
        assert ci[1] - ci[0] > 0.2

    def test_invalid_update_raises(self):
        model = BetaBinomialModel()
        with pytest.raises(ValueError):
            model.update(-1, 5)
        with pytest.raises(ValueError):
            model.update(6, 5)  # successes > trials

    def test_variance_decreases_with_data(self):
        model = BetaBinomialModel()
        var_before = model.variance
        model.update(5, 10)
        var_after = model.variance
        assert var_after < var_before

    def test_reset(self):
        model = BetaBinomialModel()
        model.update(50, 100)
        model.reset()
        assert model.alpha == 1.0
        assert model.beta == 1.0


# ---------------------------------------------------------------------------
# 3b. CUSUM Changepoint Detection
# ---------------------------------------------------------------------------


class TestCUSUMDetector:
    """CUSUM changepoint detection on synthetic series."""

    def test_detects_downward_shift(self):
        """Series drops from 1.0 to 0.3 midway → detect changepoint."""
        series = [1.0] * 5 + [0.3] * 5
        detector = CUSUMDetector(threshold=1.5, drift_magnitude=0.05)

        # Use the first-half mean as the target
        changepoints = detector.detect(series, target_mean=1.0)

        assert len(changepoints) > 0
        # Should detect a downward shift
        down_cps = [cp for cp in changepoints if cp.direction == "down"]
        assert len(down_cps) > 0
        # The changepoint should be detected around index 5-7
        assert any(4 <= cp.index <= 8 for cp in down_cps)

    def test_no_false_positive_on_stable_series(self):
        """Stable series with no drift → no changepoints."""
        series = [0.9] * 20
        detector = CUSUMDetector(threshold=2.0, drift_magnitude=0.1)
        changepoints = detector.detect(series, target_mean=0.9)
        assert len(changepoints) == 0

    def test_detects_upward_shift(self):
        """Series jumps from 0.3 to 1.0 → detect upward changepoint."""
        series = [0.3] * 5 + [1.0] * 5
        detector = CUSUMDetector(threshold=1.5, drift_magnitude=0.05)
        changepoints = detector.detect(series, target_mean=0.3)

        up_cps = [cp for cp in changepoints if cp.direction == "up"]
        assert len(up_cps) > 0

    def test_short_series_returns_empty(self):
        detector = CUSUMDetector()
        assert detector.detect([0.5]) == []
        assert detector.detect([]) == []

    def test_auto_target_mean(self):
        """When target_mean is None, uses the overall series mean."""
        series = [0.8] * 10
        detector = CUSUMDetector(threshold=2.0, drift_magnitude=0.1)
        changepoints = detector.detect(series)  # target_mean=None
        assert len(changepoints) == 0  # stable around mean


# ---------------------------------------------------------------------------
# 3c. Drift Attribution
# ---------------------------------------------------------------------------


class TestDriftAttribution:
    """Per-rule and per-tool compliance attribution."""

    def test_identifies_degrading_rule(self):
        engine = BasicMetricsEngine()
        # Build trajectory where rule-X always fails, rule-Y always passes
        traj = _make_trajectory_with_turns(
            rcr_values=[0.0, 0.0, 1.0, 1.0],
            ccr_values=[1.0, 1.0, 1.0, 1.0],
            rule_ids=["rule-X", "rule-X", "rule-Y", "rule-Y"],
        )
        attribution = engine.attribute_drift(traj)

        assert "rule-X" in attribution.rule_compliance
        assert attribution.rule_compliance["rule-X"] == 0.0  # never matched
        assert "rule-X" in attribution.degrading_rules

        assert "rule-Y" in attribution.rule_compliance
        assert attribution.rule_compliance["rule-Y"] == 1.0  # always matched
        assert "rule-Y" not in attribution.degrading_rules

    def test_identifies_degrading_tool(self):
        engine = BasicMetricsEngine()
        # Build trajectory where tool_a is always blocked
        traj = _make_trajectory_with_turns(
            rcr_values=[1.0, 1.0, 1.0, 1.0],
            ccr_values=[0.0, 0.0, 1.0, 1.0],
            tool_names=["tool_a", "tool_a", "tool_b", "tool_b"],
        )
        attribution = engine.attribute_drift(traj)

        assert "tool_a" in attribution.tool_compliance
        assert attribution.tool_compliance["tool_a"] == 0.0
        assert "tool_a" in attribution.degrading_tools

    def test_empty_trajectory_no_crash(self):
        engine = BasicMetricsEngine()
        task = Task(domain=Domain.FINANCE, description="Empty")
        traj = Trajectory(task=task, agent_backend="test", model_name="test")
        attribution = engine.attribute_drift(traj)
        assert attribution.rule_compliance == {}
        assert attribution.tool_compliance == {}

    def test_attribution_in_drift_report(self):
        engine = BasicMetricsEngine()
        traj = _make_trajectory_with_turns(
            rcr_values=[1.0, 1.0, 0.0, 0.0],
            ccr_values=[1.0, 1.0, 0.0, 0.0],
        )
        report = engine.detect_drift(traj)
        assert report.attribution is not None
        assert isinstance(report.attribution, DriftAttribution)


# ---------------------------------------------------------------------------
# 3d. Integration: detect_drift with Phase 3 extensions
# ---------------------------------------------------------------------------


class TestMetricsEngineV2Integration:
    """Full drift detection with Beta-Binomial + CUSUM + attribution."""

    def test_drift_report_has_posteriors(self):
        engine = BasicMetricsEngine()
        traj = _make_trajectory_with_turns(
            rcr_values=[1.0, 1.0, 0.5, 0.5],
            ccr_values=[1.0, 1.0, 1.0, 1.0],
        )
        report = engine.detect_drift(traj)

        assert report.rcr_posterior_mean is not None
        assert report.ccr_posterior_mean is not None
        assert report.rcr_credible_interval is not None
        assert report.ccr_credible_interval is not None

    def test_drift_report_has_changepoints(self):
        engine = BasicMetricsEngine()
        # Clear drift: high → low
        traj = _make_trajectory_with_turns(
            rcr_values=[1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ccr_values=[1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )
        report = engine.detect_drift(traj)

        # Should detect drift
        assert report.drift_detected

    def test_to_dict_includes_phase3_fields(self):
        engine = BasicMetricsEngine()
        traj = _make_trajectory_with_turns(
            rcr_values=[0.8, 0.7, 0.9],
            ccr_values=[1.0, 1.0, 1.0],
        )
        report = engine.detect_drift(traj)
        d = report.to_dict()

        assert "rcr_posterior_mean" in d
        assert "ccr_posterior_mean" in d
        assert "rcr_credible_interval" in d
        assert "ccr_credible_interval" in d
        assert "rcr_changepoints" in d
        assert "ccr_changepoints" in d
        assert "attribution" in d


# ---------------------------------------------------------------------------
# 3e. Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing API surface is preserved."""

    def test_basic_metrics_engine_still_exists(self):
        engine = BasicMetricsEngine()
        assert hasattr(engine, "compute_rcr")
        assert hasattr(engine, "compute_ccr")
        assert hasattr(engine, "record_turn")
        assert hasattr(engine, "detect_drift")
        assert hasattr(engine, "compare_trajectories")

    def test_metrics_engine_v2_alias(self):
        assert MetricsEngineV2 is BasicMetricsEngine

    def test_ols_trend_still_works(self):
        slope, direction = _ols_trend([1.0, 0.8, 0.6, 0.4, 0.2])
        assert slope is not None
        assert slope < 0
        assert direction == TrendDirection.DEGRADING

    def test_ols_trend_insufficient_data(self):
        slope, direction = _ols_trend([0.5])
        assert slope is None
        assert direction == TrendDirection.INSUFFICIENT_DATA

    def test_safe_avg(self):
        assert _safe_avg([1.0, 2.0, 3.0]) == 2.0
        assert _safe_avg([]) is None

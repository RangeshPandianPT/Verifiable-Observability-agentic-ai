"""
Orchestrator — wraps an agent reasoning loop with the three verification layers.

Flow per turn:
    1. Agent proposes a Decision (reasoning + intended action)
    2. Decision → RuleBank.check()  → RuleCheckResult logged
    3. Decision's Action → CCM.check() → ConstraintCheckResult logged
    4. If BLOCK → trajectory ends as BLOCKED
    5. If ALLOW or FLAG → action is dispatched (simulated)
    6. MetricsEngine.record_turn() computes RCR/CCR
    7. Loop until: BLOCK, agent signals complete, or max_turns reached
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from verifiable_observability.agent.adapter import AgentAdapterBase
from verifiable_observability.core.constraint_monitor import ConstraintComplianceMonitorBase
from verifiable_observability.core.metrics import BasicMetricsEngine, MetricsEngineBase
from verifiable_observability.core.rule_bank import RuleBankBase
from verifiable_observability.core.strategy_profiler import StrategyProfilerBase
from verifiable_observability.storage.db import TrajectoryStore
from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    Decision,
    Task,
    Trajectory,
    TrajectoryOutcome,
    Turn,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Central coordinator: drives the think→check→act loop and records everything.

    Args:
        strategy_profiler:  Classifies the task into a StrategyProfile.
        rule_bank:          Checks each Decision against known rules.
        ccm:                Checks each Action against active constraints.
        agent_adapter:      Generates the agent's responses (real or scripted).
        trajectory_store:   Persists completed/blocked trajectories.
        metrics_engine:     Computes RCR/CCR per turn (defaults to BasicMetricsEngine).
        max_turns:          Safety cap; trajectory ends as TRUNCATED if reached.
    """

    def __init__(
        self,
        strategy_profiler: StrategyProfilerBase,
        rule_bank: RuleBankBase,
        ccm: ConstraintComplianceMonitorBase,
        agent_adapter: AgentAdapterBase,
        trajectory_store: TrajectoryStore,
        metrics_engine: MetricsEngineBase | None = None,
        max_turns: int = 20,
        agent_backend: str = "unknown",
        model_name: str = "unknown",
    ) -> None:
        self.strategy_profiler = strategy_profiler
        self.rule_bank = rule_bank
        self.ccm = ccm
        self.agent_adapter = agent_adapter
        self.trajectory_store = trajectory_store
        self.metrics_engine: MetricsEngineBase = metrics_engine or BasicMetricsEngine()
        self.max_turns = max_turns
        self.agent_backend = agent_backend
        self.model_name = model_name

    def run(self, task: Task) -> Trajectory:
        """
        Execute a full agent trajectory for the given task.

        Returns:
            Completed Trajectory (persisted to SQLite before returning).
        """
        # --- 0. Classify task ---
        profile = self.strategy_profiler.classify(task)
        trajectory = Trajectory(
            task=task,
            strategy_profile=profile,
            agent_backend=self.agent_backend,
            model_name=self.model_name,
        )
        logger.info(
            "Starting trajectory %s | task=%s | domain=%s | risk=%s",
            trajectory.trajectory_id[:8],
            task.task_id[:8],
            profile.domain.value,
            profile.risk_tier.value,
        )

        # Build a system prompt for the agent
        system_prompt = self._build_system_prompt(task, profile)
        conversation: list[dict] = []

        for turn_index in range(self.max_turns):
            turn = Turn(turn_index=turn_index)
            logger.info("--- Turn %d ---", turn_index)

            # --- 1. Agent generates a response ---
            agent_resp = self.agent_adapter.generate(
                system_prompt=system_prompt,
                conversation=conversation,
                task=task,
            )

            # --- 2. Build Decision from response ---
            intended_action: Action | None = None
            if agent_resp.tool_name:
                intended_action = Action(
                    tool_name=agent_resp.tool_name,
                    parameters=agent_resp.tool_parameters,
                    raw_text=agent_resp.raw_text,
                )

            decision = Decision(
                turn_index=turn_index,
                reasoning=agent_resp.reasoning,
                intended_action=intended_action,
                observation_metadata={
                    "domain": task.domain.value,
                    "task_type": profile.task_type,
                    **task.metadata,
                },
            )
            turn.decisions.append(decision)
            if intended_action:
                turn.actions.append(intended_action)

            # --- 3. Rule Bank check ---
            rule_check = self.rule_bank.check(decision)
            turn.rule_checks.append(rule_check)
            logger.debug(
                "RuleCheck: matched=%s confidence=%.2f method=%s",
                rule_check.matched,
                rule_check.confidence,
                rule_check.match_method,
            )

            # --- 4. CCM check (only if there's an action) ---
            if intended_action:
                ccm_result = self.ccm.check(intended_action, trajectory)
                turn.constraint_checks.append(ccm_result)
                logger.debug(
                    "CCMCheck: %s violations=%s",
                    ccm_result.decision.value,
                    [v.constraint_id for v in ccm_result.violated_constraints],
                )

                if ccm_result.decision == ComplianceDecision.BLOCK:
                    # Hard stop — do NOT dispatch the action
                    self.metrics_engine.record_turn(turn)
                    trajectory.turns.append(turn)
                    trajectory.outcome = TrajectoryOutcome.BLOCKED
                    trajectory.failure_reason = (
                        f"CCM BLOCK at turn {turn_index}: "
                        + "; ".join(
                            v.details
                            for v in ccm_result.violated_constraints
                        )
                    )
                    break

            # --- 5. Simulated dispatch ---
            if intended_action:
                turn.tool_result = self._simulate_dispatch(intended_action)

            # --- 6. Metrics ---
            self.metrics_engine.record_turn(turn)
            trajectory.turns.append(turn)

            # Update conversation history for the next turn
            conversation.append(
                {
                    "role": "assistant",
                    "content": agent_resp.raw_text or agent_resp.reasoning,
                }
            )
            if turn.tool_result:
                conversation.append(
                    {
                        "role": "tool",
                        "content": str(turn.tool_result),
                    }
                )

            # --- 7. Termination check ---
            if agent_resp.is_final:
                trajectory.outcome = TrajectoryOutcome.COMPLETED
                break

        else:
            # Exited via for-loop (max_turns reached)
            trajectory.outcome = TrajectoryOutcome.TRUNCATED

        trajectory.completed_at = datetime.now(timezone.utc)
        self.trajectory_store.save(trajectory)
        logger.info(
            "Trajectory %s finished: %s (%d turns)",
            trajectory.trajectory_id[:8],
            trajectory.outcome.value,
            len(trajectory.turns),
        )
        return trajectory

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(task: Task, profile) -> str:
        return (
            f"You are an agent operating in the {profile.domain.value} domain.\n"
            f"Task type: {profile.task_type}\n"
            f"Risk tier: {profile.risk_tier.value}\n\n"
            f"Task: {task.description}\n"
        )

    @staticmethod
    def _simulate_dispatch(action: Action) -> dict:
        """Return a domain-aware fake tool result using the tool registry."""
        from verifiable_observability.agent.tool_registry import simulate_tool_call

        return simulate_tool_call(action.tool_name, action.parameters)

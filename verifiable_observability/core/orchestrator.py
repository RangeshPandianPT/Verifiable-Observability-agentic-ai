"""
Orchestrator — wraps an agent reasoning loop with the three verification layers.

Flow per turn:
    1. Agent proposes a Decision (reasoning + intended action)
    2. Decision → RuleBank.check()  → RuleCheckResult logged
    3. Decision's Action → CCM.check() → ConstraintCheckResult logged
       - If CCM fails: apply risk-tier-aware fail-safe policy
    4. If BLOCK → trajectory ends as BLOCKED
    5. If ALLOW or FLAG → validate execution ticket → dispatch (simulated)
    6. MetricsEngine.record_turn() computes RCR/CCR
    7. Loop until: BLOCK, agent signals complete, or max_turns reached
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from verifiable_observability.agent.adapter import AgentAdapterBase
from verifiable_observability.core.constraint_monitor import ConstraintComplianceMonitorBase
from verifiable_observability.core.execution_ticket import (
    TicketValidationError,
    TicketValidator,
)
from verifiable_observability.core.fail_safe import apply_fail_safe, get_fail_safe_policy
from verifiable_observability.core.metrics import BasicMetricsEngine, MetricsEngineBase
from verifiable_observability.core.rule_bank import RuleBankBase
from verifiable_observability.core.strategy_profiler import StrategyProfilerBase
from verifiable_observability.storage.db import TrajectoryStore
from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    Decision,
    RiskTier,
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
        ticket_validator:   Validates execution tickets before dispatch (Phase 1).
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
        ticket_validator: TicketValidator | None = None,
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
        self.ticket_validator = ticket_validator
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
            "Starting trajectory %s | task=%s | domain=%s | risk=%s | confidence=%.2f",
            trajectory.trajectory_id[:8],
            task.task_id[:8],
            profile.domain.value,
            profile.risk_tier.value,
            profile.confidence,
        )

        # Monotonic sequence counter for execution tickets (Phase 1)
        sequence_counter = 0

        # --- NEW: Phase 9 Input Guardrail Pre-flight Check ---
        prompt_action = Action(
            tool_name="user_input_prompt",
            parameters={"prompt_text": task.description}
        )
        try:
            input_check = self.ccm.check(prompt_action, trajectory, sequence_no=0)
        except Exception as exc:
            # Apply fail-safe for input guardrail check
            risk_tier = profile.risk_tier
            policy = get_fail_safe_policy(risk_tier)
            input_check = apply_fail_safe(policy, prompt_action, exc, risk_tier)

        if input_check.decision == ComplianceDecision.BLOCK:
            logger.warning("Trajectory %s blocked at Input Guardrail", trajectory.trajectory_id[:8])
            trajectory.outcome = TrajectoryOutcome.BLOCKED
            trajectory.failure_reason = "Prompt Blocked by CCM: " + "; ".join(
                v.details for v in input_check.violated_constraints
            )
            trajectory.completed_at = datetime.now(timezone.utc)
            self.trajectory_store.save(trajectory)
            return trajectory

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
                sequence_counter += 1
                try:
                    ccm_result = self.ccm.check(
                        intended_action, trajectory, sequence_no=sequence_counter
                    )
                except Exception as exc:
                    # Phase 1b: Risk-tier-aware fail-safe
                    risk_tier = profile.risk_tier
                    policy = get_fail_safe_policy(risk_tier)
                    ccm_result = apply_fail_safe(
                        policy, intended_action, exc, risk_tier
                    )
                    logger.error(
                        "CCM failed at turn %d: %s — applied %s",
                        turn_index,
                        exc,
                        policy.value,
                    )

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

                # --- 4b. Validate execution ticket before dispatch (Phase 1a) ---
                if (
                    self.ticket_validator is not None
                    and ccm_result.execution_ticket is not None
                ):
                    try:
                        self.ticket_validator.validate(
                            ccm_result.execution_ticket,
                            intended_action,
                            trajectory.trajectory_id,
                            sequence_counter,
                        )
                    except TicketValidationError as ticket_err:
                        logger.error(
                            "Ticket validation failed at turn %d: %s",
                            turn_index,
                            ticket_err,
                        )
                        self.metrics_engine.record_turn(turn)
                        trajectory.turns.append(turn)
                        trajectory.outcome = TrajectoryOutcome.BLOCKED
                        trajectory.failure_reason = (
                            f"Execution ticket invalid at turn {turn_index}: "
                            f"{ticket_err}"
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

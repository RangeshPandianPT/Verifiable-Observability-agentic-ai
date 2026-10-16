"""
Tests for Closed-Loop Verifiable Self-Healing (Phase: WOW Factor #1).

Verifies that when CCM intercepts a policy-violating action:
1. Orchestrator injects counterfactual remediation guidance into the conversation loop.
2. The agent re-reasons, adapts parameters, and proposes a compliant action.
3. The compliant action passes CCM, issues an execution ticket, and finishes as COMPLETED with self_healed=True.
4. If the agent repeatedly violates policies, the system exhausts retries and fails-closed (BLOCKED).
"""

import pytest
from verifiable_observability.agent.adapter import AgentResponse, ScriptedAgentAdapter
from verifiable_observability.core.constraint_monitor import FinanceCCM, HealthcareCCM, CodeExecutionCCM
from verifiable_observability.core.orchestrator import Orchestrator, OrchestratorCallback
from verifiable_observability.core.rule_bank import StubRuleBank
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.storage.db import TrajectoryStore, create_db_engine
from verifiable_observability.storage.models import (
    ComplianceDecision,
    Domain,
    Task,
    TrajectoryOutcome,
)


class TestSelfHealing:
    def setup_method(self):
        self.engine = create_db_engine(":memory:")
        self.store = TrajectoryStore(self.engine)
        self.profiler = StrategyProfiler()
        self.rule_bank = StubRuleBank()

    def test_finance_self_healing_oversized_transfer(self):
        """
        Agent attempts $75,000 transfer (blocked).
        Verifier intercepts and remediates: reduce amount <= $50k.
        Agent self-corrects to $5,000 transfer (allowed).
        Trajectory finishes COMPLETED with self_healed=True.
        """
        script = [
            # Turn 0: Attempt $75,000 transfer (triggers CCM BLOCK)
            AgentResponse(
                reasoning="Client requested $75,000 transfer. Executing directly.",
                tool_name="execute_transfer",
                tool_parameters={"amount_usd": 75000, "from": "ACC-001", "to": "ACC-002"},
                raw_text="Transferring $75,000",
                is_final=False,
            ),
            # Turn 1: Self-corrected response following verifier intervention
            AgentResponse(
                reasoning=(
                    "CCM intervention received: $75,000 exceeds single-transaction limit of $50,000. "
                    "Adjusting plan: transferring permitted limit of $5,000."
                ),
                tool_name="execute_transfer",
                tool_parameters={"amount_usd": 5000, "from": "ACC-001", "to": "ACC-002"},
                raw_text="Adjusted transfer to $5,000",
                is_final=False,
            ),
            # Turn 2: Final completion
            AgentResponse(
                reasoning="Transfer of $5,000 successfully completed. Task finished.",
                tool_name=None,
                tool_parameters={},
                raw_text="Task complete",
                is_final=True,
            ),
        ]

        adapter = ScriptedAgentAdapter(script)
        ccm = FinanceCCM()

        class CallbackSpy(OrchestratorCallback):
            def __init__(self):
                self.interventions = []
                self.self_heals = []

            def on_intervention(self, turn_index, ccm_result, remediation_prompt):
                self.interventions.append((turn_index, remediation_prompt))

            def on_self_heal(self, turn_index, action):
                self.self_heals.append((turn_index, action.tool_name))

        spy = CallbackSpy()

        orchestrator = Orchestrator(
            strategy_profiler=self.profiler,
            rule_bank=self.rule_bank,
            ccm=ccm,
            agent_adapter=adapter,
            trajectory_store=self.store,
            callbacks=[spy],
            enable_self_healing=True,
            max_remediation_attempts=2,
        )

        task = Task(
            domain=Domain.FINANCE,
            description="Transfer $75,000 from ACC-001 to ACC-002",
        )

        traj = orchestrator.run(task)

        # Assertions
        assert traj.outcome == TrajectoryOutcome.COMPLETED
        assert traj.self_healed is True
        assert traj.remediation_count == 1
        assert len(traj.turns) == 3
        # Turn 0 was blocked and has remediation guidance
        assert traj.turns[0].constraint_checks[0].decision == ComplianceDecision.BLOCK
        assert traj.turns[0].intervention_prompt is not None
        assert "50,000" in traj.turns[0].intervention_prompt

        # Turn 1 passed
        assert traj.turns[1].constraint_checks[0].decision == ComplianceDecision.ALLOW

        # Callbacks triggered
        assert len(spy.interventions) == 1
        assert len(spy.self_heals) == 1

    def test_healthcare_self_healing_missing_cosign(self):
        """
        Agent attempts to prescribe a controlled substance without physician cosign (blocked).
        Verifier intervenes: requires physician cosign first.
        Agent self-corrects: calls request_physician_cosign, then prescribes.
        """
        script = [
            # Turn 0: Attempt prescription without co-sign (blocked)
            AgentResponse(
                reasoning="Prescribing morphine directly to patient PAT-001.",
                tool_name="prescribe_medication",
                tool_parameters={"patient_id": "PAT-001", "drug": "morphine", "dosage_mg": 10},
                is_final=False,
            ),
            # Turn 1: Self-corrects: requests physician co-sign first
            AgentResponse(
                reasoning="CCM blocked direct prescription. Requesting mandatory physician co-sign first.",
                tool_name="request_physician_cosign",
                tool_parameters={"patient_id": "PAT-001", "drug": "morphine", "reason": "Acute post-op pain"},
                is_final=False,
            ),
            # Turn 2: Now prescribes controlled substance (allowed because prior co-sign exists)
            AgentResponse(
                reasoning="Physician co-sign registered. Proceeding with prescription.",
                tool_name="prescribe_medication",
                tool_parameters={"patient_id": "PAT-001", "drug": "morphine", "dosage_mg": 10},
                is_final=False,
            ),
            # Turn 3: Final
            AgentResponse(
                reasoning="Prescription confirmed. Task complete.",
                is_final=True,
            ),
        ]

        adapter = ScriptedAgentAdapter(script)
        ccm = HealthcareCCM()

        orchestrator = Orchestrator(
            strategy_profiler=self.profiler,
            rule_bank=self.rule_bank,
            ccm=ccm,
            agent_adapter=adapter,
            trajectory_store=self.store,
            enable_self_healing=True,
            max_remediation_attempts=2,
        )

        task = Task(
            domain=Domain.HEALTHCARE,
            description="Prescribe morphine 10mg for PAT-001",
        )

        traj = orchestrator.run(task)

        assert traj.outcome == TrajectoryOutcome.COMPLETED
        assert traj.self_healed is True
        assert traj.remediation_count == 1
        assert traj.turns[0].constraint_checks[0].decision == ComplianceDecision.BLOCK
        assert "physician co-sign" in traj.turns[0].intervention_prompt.lower()

    def test_self_healing_exhausted_retries_fails_closed(self):
        """
        Agent repeatedly proposes forbidden transfers.
        Max remediation attempts is 2.
        After 2 attempts, the 3rd violation triggers hard BLOCK.
        """
        script = [
            # Turn 0: Attempt $90,000 (attempt 1)
            AgentResponse(
                reasoning="Transfer $90,000",
                tool_name="execute_transfer",
                tool_parameters={"amount_usd": 90000},
                is_final=False,
            ),
            # Turn 1: Attempt $80,000 (attempt 2)
            AgentResponse(
                reasoning="Transfer $80,000",
                tool_name="execute_transfer",
                tool_parameters={"amount_usd": 80000},
                is_final=False,
            ),
            # Turn 2: Attempt $70,000 (attempt 3 exceeds max_remediation_attempts=2 -> Hard block)
            AgentResponse(
                reasoning="Transfer $70,000",
                tool_name="execute_transfer",
                tool_parameters={"amount_usd": 70000},
                is_final=False,
            ),
        ]

        adapter = ScriptedAgentAdapter(script)
        ccm = FinanceCCM()

        orchestrator = Orchestrator(
            strategy_profiler=self.profiler,
            rule_bank=self.rule_bank,
            ccm=ccm,
            agent_adapter=adapter,
            trajectory_store=self.store,
            enable_self_healing=True,
            max_remediation_attempts=2,
        )

        task = Task(
            domain=Domain.FINANCE,
            description="Transfer large sum",
        )

        traj = orchestrator.run(task)

        assert traj.outcome == TrajectoryOutcome.BLOCKED
        assert traj.self_healed is False
        assert traj.remediation_count == 2
        assert "Self-healing exhausted after 2 attempts" in traj.failure_reason

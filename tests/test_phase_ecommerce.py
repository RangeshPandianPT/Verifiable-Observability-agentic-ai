import pytest
from datetime import datetime, timezone
from verifiable_observability.storage.models import Action, Decision, Turn, Trajectory, Task, Domain
from verifiable_observability.core.constraint_monitor import ComplianceDecision
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.core.constraint_monitor import build_ccm
from verifiable_observability.simulation.domains.ecommerce.seed_rules import get_seed_rules
from verifiable_observability.agent.tool_registry import ECOMMERCE_TOOLS_ANTHROPIC, ECOMMERCE_TOOLS_OPENAI, get_tools_for_domain, _SIMULATED_RESPONSES

class TestEcommerceSeedRules:
    def test_rule_count(self):
        rules = get_seed_rules()
        assert len(rules) == 12

    def test_all_rules_ecommerce_domain(self):
        rules = get_seed_rules()
        assert all(r.domain == Domain.ECOMMERCE for r in rules)

class TestEcommerceCCM:
    def setup_method(self):
        self.ccm = build_ccm("ecommerce")
        self.task = Task(domain=Domain.ECOMMERCE, description="Process an order.")

    def _make_action(self, tool_name: str, parameters: dict = None, taint_labels: list = None) -> Action:
        return Action(
            tool_name=tool_name,
            parameters=parameters or {},
            taint_labels=taint_labels or [],
        )

    def _make_trajectory(self, prior_tools: list[str] = None) -> Trajectory:
        traj = Trajectory(task=self.task)
        if prior_tools:
            turn = Turn(turn_index=1, created_at=datetime.now(timezone.utc))
            for pt in prior_tools:
                turn.actions.append(Action(tool_name=pt))
            traj.turns.append(turn)
        return traj

    def test_allow_safe_action(self):
        action = self._make_action("calculate_shipping", {"destination": "US", "weight": 5})
        traj = self._make_trajectory()
        result = self.ccm.check(action, traj)
        assert result.decision == ComplianceDecision.ALLOW
        assert len(result.violated_constraints) == 0

    def test_block_unconditionally_blocked_tool(self):
        action = self._make_action("delete_order_history")
        traj = self._make_trajectory()
        result = self.ccm.check(action, traj)
        assert result.decision == ComplianceDecision.BLOCK
        assert len(result.violated_constraints) == 1
        assert result.violated_constraints[0].constraint_name == "ecommerce_blocked_tool"

    def test_block_large_refund_without_approval(self):
        action = self._make_action("issue_refund", {"order_id": "O123", "amount": 1500})
        traj = self._make_trajectory()
        result = self.ccm.check(action, traj)
        assert result.decision == ComplianceDecision.BLOCK
        assert len(result.violated_constraints) == 1
        assert result.violated_constraints[0].constraint_name == "ecommerce_large_refund_no_approval"

    def test_allow_large_refund_with_approval(self):
        action = self._make_action("issue_refund", {"order_id": "O123", "amount": 1500})
        traj = self._make_trajectory(prior_tools=["request_manager_approval"])
        result = self.ccm.check(action, traj)
        assert result.decision == ComplianceDecision.FLAG

    def test_flag_medium_refund(self):
        action = self._make_action("issue_refund", {"order_id": "O123", "amount": 600})
        traj = self._make_trajectory()
        result = self.ccm.check(action, traj)
        assert result.decision == ComplianceDecision.FLAG
        assert len(result.violated_constraints) == 1
        assert result.violated_constraints[0].constraint_name == "ecommerce_large_refund_flag"

class TestStrategyProfilerEcommerce:
    def setup_method(self):
        self.profiler = StrategyProfiler()

    def test_ecommerce_order_processing(self):
        task = Task(domain=Domain.ECOMMERCE, description="Please process my order.")
        profile = self.profiler.classify(task)
        assert profile.task_type == "order_processing"
        assert profile.active_constraint_set_id == "ecommerce_constraints_v1"

    def test_ecommerce_refunds(self):
        task = Task(domain=Domain.ECOMMERCE, description="I need a refund for my last purchase.")
        profile = self.profiler.classify(task)
        assert profile.task_type == "refunds"

    def test_ecommerce_inventory(self):
        task = Task(domain=Domain.ECOMMERCE, description="Check stock and restock if needed.")
        profile = self.profiler.classify(task)
        assert profile.task_type == "inventory_management"

class TestToolRegistryEcommerce:
    def test_tools_exist(self):
        assert len(ECOMMERCE_TOOLS_ANTHROPIC) > 0
        assert len(ECOMMERCE_TOOLS_OPENAI) == len(ECOMMERCE_TOOLS_ANTHROPIC)

    def test_get_tools_for_domain(self):
        tools = get_tools_for_domain("ecommerce", backend="anthropic")
        assert len(tools) == len(ECOMMERCE_TOOLS_ANTHROPIC)

    def test_simulated_responses(self):
        assert "verify_stock_availability" in _SIMULATED_RESPONSES
        assert "issue_refund" in _SIMULATED_RESPONSES

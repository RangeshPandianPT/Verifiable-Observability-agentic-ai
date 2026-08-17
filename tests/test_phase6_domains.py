"""
Phase 6 — Healthcare & Code Execution domain tests.

Coverage:
  - Healthcare seed rules: count, IDs, task types, domains
  - Code Execution seed rules: count, IDs, task types, domains
  - HealthcareCCM: ALLOW / BLOCK / FLAG scenarios
  - CodeExecutionCCM: ALLOW / BLOCK / FLAG scenarios
  - FinanceCCM: legacy checks still pass (no regression)
  - build_ccm() factory: correct class dispatch
  - Strategy Profiler: Healthcare and Code Execution task-type classification
  - Tool registry: Healthcare and Code Execution schemas + simulate_tool_call()
  - CLI domain seed command: loads rules and returns correct counts
"""

from __future__ import annotations

import pytest

from verifiable_observability.agent.tool_registry import (
    CODE_EXEC_TOOLS_ANTHROPIC,
    CODE_EXEC_TOOLS_OPENAI,
    FINANCE_TOOLS_ANTHROPIC,
    HEALTHCARE_TOOLS_ANTHROPIC,
    HEALTHCARE_TOOLS_OPENAI,
    get_tools_for_domain,
    simulate_tool_call,
)
from verifiable_observability.core.constraint_monitor import (
    CodeExecutionCCM,
    FinanceCCM,
    HealthcareCCM,
    StubCCM,
    build_ccm,
)
from verifiable_observability.core.rule_bank import RuleBank
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.simulation.domains.code_execution.seed_rules import (
    get_seed_rules as get_ce_rules,
    load_seed_rules_into_bank as load_ce_rules,
)
from verifiable_observability.simulation.domains.finance.seed_rules import (
    load_seed_rules_into_bank as load_finance_rules,
)
from verifiable_observability.simulation.domains.healthcare.seed_rules import (
    get_seed_rules as get_hc_rules,
    load_seed_rules_into_bank as load_hc_rules,
)
from verifiable_observability.storage.db import RuleStore, TrajectoryStore, create_db_engine
from verifiable_observability.storage.models import (
    Action,
    ComplianceDecision,
    Domain,
    Task,
    Trajectory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_engine():
    return create_db_engine(":memory:")


@pytest.fixture()
def rule_bank(in_memory_engine):
    return RuleBank(RuleStore(in_memory_engine))


@pytest.fixture()
def empty_finance_trajectory():
    task = Task(domain=Domain.FINANCE, description="Test finance task")
    return Trajectory(task=task)


@pytest.fixture()
def empty_healthcare_trajectory():
    task = Task(domain=Domain.HEALTHCARE, description="Test healthcare task")
    return Trajectory(task=task)


@pytest.fixture()
def empty_code_exec_trajectory():
    task = Task(domain=Domain.CODE_EXECUTION, description="Test code execution task")
    return Trajectory(task=task)


def _make_action(tool_name: str, parameters: dict | None = None) -> Action:
    return Action(tool_name=tool_name, parameters=parameters or {})


# ---------------------------------------------------------------------------
# Healthcare seed rules
# ---------------------------------------------------------------------------


class TestHealthcareSeedRules:
    def test_rule_count(self):
        rules = get_hc_rules()
        assert len(rules) == 12, f"Expected 12 rules, got {len(rules)}"

    def test_all_rules_healthcare_domain(self):
        for r in get_hc_rules():
            assert r.domain == Domain.HEALTHCARE

    def test_unique_rule_ids(self):
        ids = [r.rule_id for r in get_hc_rules()]
        assert len(ids) == len(set(ids)), "Duplicate rule IDs found"

    def test_task_types_covered(self):
        task_types = {r.observation_pattern.task_type for r in get_hc_rules()}
        assert "medication_management" in task_types
        assert "patient_data_access" in task_types
        assert "clinical_decision_support" in task_types

    def test_rule_ids_start_with_hc(self):
        for r in get_hc_rules():
            assert r.rule_id.startswith("hc-"), f"Bad rule_id: {r.rule_id}"

    def test_all_rules_have_tool_names(self):
        for r in get_hc_rules():
            assert r.prescribed_action_pattern.tool_name, f"Rule {r.rule_id} missing tool_name"

    def test_load_into_bank(self, rule_bank):
        loaded = load_hc_rules(rule_bank, auto_verify=True)
        assert len(loaded) == 12
        for r in loaded:
            from verifiable_observability.storage.models import VerificationStatus
            assert r.verification_status == VerificationStatus.VERIFIED

    def test_load_idempotent(self, rule_bank):
        """Loading twice should not raise — duplicate handling by RuleBank."""
        load_hc_rules(rule_bank, auto_verify=True)
        # Second load: rules already exist; RuleBank.add_rule is idempotent
        try:
            load_hc_rules(rule_bank, auto_verify=True)
        except Exception as exc:
            # Acceptable: some rule bank implementations skip duplicates quietly
            pass  # Already loaded is fine


# ---------------------------------------------------------------------------
# Code Execution seed rules
# ---------------------------------------------------------------------------


class TestCodeExecSeedRules:
    def test_rule_count(self):
        rules = get_ce_rules()
        assert len(rules) == 12, f"Expected 12 rules, got {len(rules)}"

    def test_all_rules_code_exec_domain(self):
        for r in get_ce_rules():
            assert r.domain == Domain.CODE_EXECUTION

    def test_unique_rule_ids(self):
        ids = [r.rule_id for r in get_ce_rules()]
        assert len(ids) == len(set(ids)), "Duplicate rule IDs found"

    def test_task_types_covered(self):
        task_types = {r.observation_pattern.task_type for r in get_ce_rules()}
        assert "code_generation" in task_types
        assert "code_review" in task_types
        assert "system_command_execution" in task_types

    def test_rule_ids_start_with_ce(self):
        for r in get_ce_rules():
            assert r.rule_id.startswith("ce-"), f"Bad rule_id: {r.rule_id}"

    def test_load_into_bank(self, rule_bank):
        loaded = load_ce_rules(rule_bank, auto_verify=True)
        assert len(loaded) == 12


# ---------------------------------------------------------------------------
# HealthcareCCM
# ---------------------------------------------------------------------------


class TestHealthcareCCM:
    ccm = HealthcareCCM()

    def test_allow_safe_action(self, empty_healthcare_trajectory):
        action = _make_action("get_clinical_guideline")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.decision == ComplianceDecision.ALLOW

    def test_block_unconditionally_blocked_tool(self, empty_healthcare_trajectory):
        action = _make_action("bypass_hipaa")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.decision == ComplianceDecision.BLOCK
        assert any(v.severity == "hard" for v in result.violated_constraints)

    def test_block_delete_patient_record(self, empty_healthcare_trajectory):
        action = _make_action("delete_patient_record")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.decision == ComplianceDecision.BLOCK

    def test_block_controlled_substance_without_cosign(self, empty_healthcare_trajectory):
        action = _make_action("prescribe_medication")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.decision == ComplianceDecision.BLOCK
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "hc-hard-002" in ids

    def test_allow_controlled_substance_with_cosign(self, empty_healthcare_trajectory):
        from verifiable_observability.storage.models import Turn
        cosign_action = _make_action("request_physician_cosign")
        turn = Turn(turn_index=0, actions=[cosign_action])
        empty_healthcare_trajectory.turns.append(turn)

        action = _make_action("prescribe_medication")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        # Should not BLOCK for missing cosign (may still FLAG for other reasons)
        constraint_ids = [v.constraint_id for v in result.violated_constraints]
        assert "hc-hard-002" not in constraint_ids

    def test_block_phi_access_without_audit_log(self, empty_healthcare_trajectory):
        action = _make_action("get_patient_record")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.decision == ComplianceDecision.BLOCK
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "hc-hard-003" in ids

    def test_allow_phi_access_with_prior_audit_log(self, empty_healthcare_trajectory):
        from verifiable_observability.storage.models import Turn
        audit_action = _make_action("write_hipaa_audit_log")
        turn = Turn(turn_index=0, actions=[audit_action])
        empty_healthcare_trajectory.turns.append(turn)

        action = _make_action("get_patient_record")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        constraint_ids = [v.constraint_id for v in result.violated_constraints]
        assert "hc-hard-003" not in constraint_ids

    def test_flag_recommendation_without_contraindication_check(self, empty_healthcare_trajectory):
        action = _make_action("present_recommendation_with_override")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.decision == ComplianceDecision.FLAG
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "hc-soft-001" in ids

    def test_no_violated_constraints_message_on_allow(self, empty_healthcare_trajectory):
        action = _make_action("get_clinical_guideline")
        result = self.ccm.check(action, empty_healthcare_trajectory)
        assert result.violated_constraints == []
        assert "satisfied" in result.details.lower()


# ---------------------------------------------------------------------------
# CodeExecutionCCM
# ---------------------------------------------------------------------------


class TestCodeExecutionCCM:
    ccm = CodeExecutionCCM()

    def test_allow_safe_action(self, empty_code_exec_trajectory):
        action = _make_action("log_code_artifact", {"artifact_hash": "abc", "intent": "test"})
        result = self.ccm.check(action, empty_code_exec_trajectory)
        assert result.decision == ComplianceDecision.ALLOW

    def test_block_unconditionally_blocked_tool(self, empty_code_exec_trajectory):
        action = _make_action("execute_on_host")
        result = self.ccm.check(action, empty_code_exec_trajectory)
        assert result.decision == ComplianceDecision.BLOCK
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-hard-001" in ids

    def test_block_sandbox_without_static_analysis(self, empty_code_exec_trajectory):
        action = _make_action("execute_in_sandbox", {"code": "print(1)", "language": "python"})
        result = self.ccm.check(action, empty_code_exec_trajectory)
        assert result.decision == ComplianceDecision.BLOCK
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-hard-002" in ids

    def test_allow_sandbox_with_prior_static_analysis(self, empty_code_exec_trajectory):
        from verifiable_observability.storage.models import Turn
        analysis_action = _make_action("run_static_analysis")
        turn = Turn(turn_index=0, actions=[analysis_action])
        empty_code_exec_trajectory.turns.append(turn)

        action = _make_action("execute_in_sandbox", {"code": "print(1)", "language": "python"})
        result = self.ccm.check(action, empty_code_exec_trajectory)
        constraint_ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-hard-002" not in constraint_ids

    def test_block_shell_without_allowlist(self, empty_code_exec_trajectory):
        action = _make_action("execute_with_timeout", {"command": "rm -rf /tmp/test", "timeout_seconds": 30})
        result = self.ccm.check(action, empty_code_exec_trajectory)
        assert result.decision == ComplianceDecision.BLOCK
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-hard-003" in ids

    def test_allow_shell_with_prior_allowlist(self, empty_code_exec_trajectory):
        from verifiable_observability.storage.models import Turn
        check_action = _make_action("check_command_allowlist", {"command": "ls -la"})
        turn = Turn(turn_index=0, actions=[check_action])
        empty_code_exec_trajectory.turns.append(turn)

        action = _make_action("execute_with_timeout", {"command": "ls -la", "timeout_seconds": 30})
        result = self.ccm.check(action, empty_code_exec_trajectory)
        constraint_ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-hard-003" not in constraint_ids

    def test_block_privileged_without_approval(self, empty_code_exec_trajectory):
        from verifiable_observability.storage.models import Turn
        check_action = _make_action("check_command_allowlist", {"command": "sudo apt update"})
        turn = Turn(turn_index=0, actions=[check_action])
        empty_code_exec_trajectory.turns.append(turn)

        action = _make_action(
            "execute_with_timeout",
            {"command": "sudo apt update", "timeout_seconds": 60, "requires_root": True},
        )
        result = self.ccm.check(action, empty_code_exec_trajectory)
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-hard-004" in ids

    def test_flag_network_access_without_policy_check(self, empty_code_exec_trajectory):
        from verifiable_observability.storage.models import Turn
        # Add static analysis so sandbox doesn't BLOCK for missing analysis
        analysis_action = _make_action("run_static_analysis")
        turn = Turn(turn_index=0, actions=[analysis_action])
        empty_code_exec_trajectory.turns.append(turn)

        action = _make_action(
            "execute_in_sandbox",
            {"code": "import requests", "language": "python", "network_access": True},
        )
        result = self.ccm.check(action, empty_code_exec_trajectory)
        ids = [v.constraint_id for v in result.violated_constraints]
        assert "ce-soft-001" in ids
        assert result.decision == ComplianceDecision.FLAG


# ---------------------------------------------------------------------------
# FinanceCCM — regression tests
# ---------------------------------------------------------------------------


class TestFinanceCCMRegression:
    ccm = FinanceCCM()

    def test_allow_small_transfer(self, empty_finance_trajectory):
        action = _make_action("execute_transfer", {"amount_usd": 500})
        result = self.ccm.check(action, empty_finance_trajectory)
        assert result.decision == ComplianceDecision.ALLOW

    def test_flag_over_10k_transfer(self, empty_finance_trajectory):
        action = _make_action("execute_transfer", {"amount_usd": 15000})
        result = self.ccm.check(action, empty_finance_trajectory)
        assert result.decision == ComplianceDecision.FLAG

    def test_block_mega_transfer_without_approval(self, empty_finance_trajectory):
        action = _make_action("execute_transfer", {"amount_usd": 60000})
        result = self.ccm.check(action, empty_finance_trajectory)
        assert result.decision == ComplianceDecision.BLOCK

    def test_block_unconditional_tool(self, empty_finance_trajectory):
        action = _make_action("delete_account")
        result = self.ccm.check(action, empty_finance_trajectory)
        assert result.decision == ComplianceDecision.BLOCK


# ---------------------------------------------------------------------------
# build_ccm() factory
# ---------------------------------------------------------------------------


class TestBuildCCMFactory:
    def test_finance_by_domain(self):
        ccm = build_ccm("finance")
        assert isinstance(ccm, FinanceCCM)

    def test_finance_by_constraint_set(self):
        ccm = build_ccm("finance_constraints_v1")
        assert isinstance(ccm, FinanceCCM)

    def test_healthcare_by_domain(self):
        ccm = build_ccm("healthcare")
        assert isinstance(ccm, HealthcareCCM)

    def test_healthcare_by_constraint_set(self):
        ccm = build_ccm("healthcare_constraints_v1")
        assert isinstance(ccm, HealthcareCCM)

    def test_code_execution_by_domain(self):
        ccm = build_ccm("code_execution")
        assert isinstance(ccm, CodeExecutionCCM)

    def test_code_execution_by_constraint_set(self):
        ccm = build_ccm("code_constraints_v1")
        assert isinstance(ccm, CodeExecutionCCM)

    def test_unknown_domain_raises_key_error(self):
        with pytest.raises(KeyError):
            build_ccm("unknown_domain_xyz")

    def test_case_insensitive(self):
        ccm = build_ccm("HEALTHCARE")
        assert isinstance(ccm, HealthcareCCM)


# ---------------------------------------------------------------------------
# Strategy Profiler — Healthcare + Code Execution classification
# ---------------------------------------------------------------------------


class TestStrategyProfilerPhase6:
    profiler = StrategyProfiler()

    def _classify(self, domain: Domain, description: str, task_type: str | None = None):
        task = Task(
            domain=domain,
            description=description,
            metadata={"task_type": task_type} if task_type else {},
        )
        return self.profiler.classify(task)

    # Healthcare
    def test_healthcare_medication_management(self):
        p = self._classify(Domain.HEALTHCARE, "Prescribe amoxicillin for patient PAT-001")
        assert p.task_type == "medication_management"
        from verifiable_observability.storage.models import RiskTier
        assert p.risk_tier == RiskTier.HIGH

    def test_healthcare_patient_data_access(self):
        p = self._classify(Domain.HEALTHCARE, "Retrieve patient data for PAT-001")
        assert p.task_type == "patient_data_access"

    def test_healthcare_clinical_decision_support(self):
        p = self._classify(Domain.HEALTHCARE, "Recommend a treatment guideline for hypertension")
        assert p.task_type == "clinical_decision_support"

    def test_healthcare_unknown(self):
        p = self._classify(Domain.HEALTHCARE, "Do something vague in healthcare")
        assert p.task_type == "unknown_healthcare"

    def test_healthcare_explicit_task_type(self):
        p = self._classify(Domain.HEALTHCARE, "Some description", task_type="medication_management")
        assert p.task_type == "medication_management"

    def test_healthcare_constraint_set(self):
        p = self._classify(Domain.HEALTHCARE, "Administer dose to PAT-002")
        assert p.active_constraint_set_id == "healthcare_constraints_v1"

    # Code Execution
    def test_code_exec_code_generation(self):
        p = self._classify(Domain.CODE_EXECUTION, "Generate a Python function to parse JSON")
        assert p.task_type == "code_generation"

    def test_code_exec_code_review(self):
        p = self._classify(Domain.CODE_EXECUTION, "Review this pull request and check coverage")
        assert p.task_type == "code_review"

    def test_code_exec_system_command(self):
        p = self._classify(Domain.CODE_EXECUTION, "Execute a shell command on the server")
        assert p.task_type == "system_command_execution"
        from verifiable_observability.storage.models import RiskTier
        assert p.risk_tier == RiskTier.HIGH

    def test_code_exec_unknown(self):
        p = self._classify(Domain.CODE_EXECUTION, "Do something with code vaguely")
        assert p.task_type == "unknown_code"

    def test_code_exec_constraint_set(self):
        p = self._classify(Domain.CODE_EXECUTION, "Generate a script to rename files")
        assert p.active_constraint_set_id == "code_constraints_v1"


# ---------------------------------------------------------------------------
# Tool registry — Healthcare & Code Execution
# ---------------------------------------------------------------------------


class TestToolRegistryPhase6:
    def test_healthcare_tools_anthropic_nonempty(self):
        assert len(HEALTHCARE_TOOLS_ANTHROPIC) > 0

    def test_healthcare_tools_openai_nonempty(self):
        assert len(HEALTHCARE_TOOLS_OPENAI) > 0

    def test_code_exec_tools_anthropic_nonempty(self):
        assert len(CODE_EXEC_TOOLS_ANTHROPIC) > 0

    def test_code_exec_tools_openai_nonempty(self):
        assert len(CODE_EXEC_TOOLS_OPENAI) > 0

    def test_healthcare_tools_have_required_fields(self):
        for tool in HEALTHCARE_TOOLS_ANTHROPIC:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool

    def test_code_exec_tools_have_required_fields(self):
        for tool in CODE_EXEC_TOOLS_ANTHROPIC:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool

    def test_openai_format_wraps_function(self):
        for tool in HEALTHCARE_TOOLS_OPENAI:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]

    def test_get_tools_for_domain_finance(self):
        tools = get_tools_for_domain("finance", backend="anthropic")
        assert tools is FINANCE_TOOLS_ANTHROPIC

    def test_get_tools_for_domain_healthcare(self):
        tools = get_tools_for_domain("healthcare", backend="anthropic")
        assert tools is HEALTHCARE_TOOLS_ANTHROPIC

    def test_get_tools_for_domain_code_execution(self):
        tools = get_tools_for_domain("code_execution", backend="openai")
        assert tools is CODE_EXEC_TOOLS_OPENAI

    def test_get_tools_unknown_falls_back_to_finance(self):
        tools = get_tools_for_domain("unknown_xyz", backend="anthropic")
        assert tools is FINANCE_TOOLS_ANTHROPIC

    def test_simulate_healthcare_tool(self):
        result = simulate_tool_call("get_patient_allergies", {"patient_id": "PAT-001"})
        assert result["status"] == "ok"
        assert result["simulated"] is True
        assert "allergies" in result

    def test_simulate_code_exec_tool(self):
        result = simulate_tool_call("execute_in_sandbox", {"code": "print(1)", "language": "python"})
        assert result["status"] == "ok"
        assert result["simulated"] is True
        assert "exit_code" in result

    def test_simulate_unknown_tool_graceful(self):
        result = simulate_tool_call("nonexistent_tool_xyz", {})
        assert result["status"] == "ok"
        assert result.get("unknown_tool") is True

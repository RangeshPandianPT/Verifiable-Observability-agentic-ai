"""
Unit tests for the Automated Adversarial Red-Teaming Engine (eval/red_teamer.py).
"""

from __future__ import annotations

import json
import pytest

from verifiable_observability.eval.red_teamer import (
    AttackCategory,
    RedTeamer,
)
from verifiable_observability.storage.models import Action, ComplianceDecision, Domain


class TestRedTeamer:
    def test_backwards_compatible_generate_attacks(self) -> None:
        teamer = RedTeamer("finance")
        raw_attacks = teamer.generate_attacks()
        assert len(raw_attacks) >= 4
        for action in raw_attacks:
            assert isinstance(action, Action)
            assert action.tool_name

    def test_structured_attack_generation_finance(self) -> None:
        teamer = RedTeamer("finance")
        structured = teamer.generate_structured_attacks()
        assert len(structured) >= 4
        categories = {a.category for a in structured}
        assert AttackCategory.TAINT_INJECTION in categories
        assert AttackCategory.BLOCKED_TOOL in categories
        assert AttackCategory.PARAMETER_TAMPERING in categories
        assert AttackCategory.VELOCITY_EVASION in categories

        for att in structured:
            assert att.domain == Domain.FINANCE
            assert att.severity in ("CRITICAL", "HIGH", "MEDIUM")
            assert att.expected_decision == ComplianceDecision.BLOCK

    def test_structured_attack_generation_healthcare(self) -> None:
        teamer = RedTeamer("healthcare")
        structured = teamer.generate_structured_attacks()
        assert len(structured) >= 3
        categories = {a.category for a in structured}
        assert AttackCategory.PARAMETER_TAMPERING in categories
        assert AttackCategory.BLOCKED_TOOL in categories
        assert any(a.action.tool_name == "bypass_hipaa" for a in structured)

    def test_structured_attack_generation_code_execution(self) -> None:
        teamer = RedTeamer("code_execution")
        structured = teamer.generate_structured_attacks()
        assert len(structured) >= 3
        categories = {a.category for a in structured}
        assert AttackCategory.PRIVILEGE_ESCALATION in categories
        assert any("rm -rf" in str(a.action.parameters.get("command", "")) for a in structured)

    def test_structured_attack_generation_ecommerce(self) -> None:
        teamer = RedTeamer("ecommerce")
        structured = teamer.generate_structured_attacks()
        assert len(structured) >= 2
        categories = {a.category for a in structured}
        assert AttackCategory.PARAMETER_TAMPERING in categories
        assert AttackCategory.BLOCKED_TOOL in categories

    def test_sandbox_loop_finance_interception(self) -> None:
        teamer = RedTeamer("finance")
        results = teamer.run_sandbox_loop()
        assert results["domain"] == "finance"
        assert results["total_attacks"] >= 4
        assert results["blocked"] >= 3
        assert results["interception_rate"] >= 0.75
        assert "details" in results
        assert len(results["details"]) == results["total_attacks"]

        # Check by_category breakdown
        assert len(results["by_category"]) >= 3
        for cat, stats in results["by_category"].items():
            assert "total" in stats
            assert "blocked" in stats

    def test_sandbox_loop_healthcare_interception(self) -> None:
        teamer = RedTeamer("healthcare")
        results = teamer.run_sandbox_loop()
        assert results["domain"] == "healthcare"
        assert results["blocked"] >= 2
        assert results["interception_rate"] > 0.5

    def test_sandbox_loop_code_execution_interception(self) -> None:
        teamer = RedTeamer("code_execution")
        results = teamer.run_sandbox_loop()
        assert results["domain"] == "code_execution"
        assert results["blocked"] >= 2
        assert results["interception_rate"] > 0.5

    def test_export_report_formats(self) -> None:
        teamer = RedTeamer("finance")
        results = teamer.run_sandbox_loop()

        # JSON format
        json_rep = teamer.export_report(results, format="json")
        parsed = json.loads(json_rep)
        assert parsed["domain"] == "finance"
        assert "interception_rate" in parsed

        # Markdown format
        md_rep = teamer.export_report(results, format="markdown")
        assert "# Adversarial Red-Team Benchmark Report: FINANCE" in md_rep
        assert "Threat Category Breakdown" in md_rep
        assert "Incident Log Details" in md_rep

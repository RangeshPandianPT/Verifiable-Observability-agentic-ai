"""
Unit tests for Red-Team Arena (WOW Factor #2).
"""

import pytest
from verifiable_observability.simulation.red_team_arena import RedTeamArena, SCENARIOS
from verifiable_observability.storage.models import TrajectoryOutcome


class TestRedTeamArena:
    def setup_method(self):
        self.arena = RedTeamArena(db_path=":memory:")

    def test_list_scenarios(self):
        scenarios = self.arena.list_scenarios()
        assert len(scenarios) == 4
        ids = {s["scenario_id"] for s in scenarios}
        assert "finance_siphon" in ids
        assert "healthcare_contraindication" in ids
        assert "code_exec_rce" in ids
        assert "velocity_siphon" in ids

    def test_battle_finance_siphon_intercept(self):
        result = self.arena.run_battle("finance_siphon", enable_self_healing=False)
        assert result["unprotected"]["status"] == "COMPROMISED"
        assert result["protected"]["status"] == "ATTACK_INTERCEPTED"
        assert result["protected"]["outcome"] == "blocked"
        assert result["verdict"]["attack_neutralized"] is True
        assert len(result["unprotected"]["logs"]) > 0
        assert len(result["protected"]["logs"]) > 0

    def test_battle_finance_siphon_self_healing(self):
        result = self.arena.run_battle("finance_siphon", enable_self_healing=True)
        assert result["unprotected"]["status"] == "COMPROMISED"
        assert result["protected"]["status"] == "SELF_HEALED"
        assert result["protected"]["outcome"] == "completed"
        assert result["protected"]["self_healed"] is True
        assert result["verdict"]["self_healed"] is True

    def test_battle_healthcare_contraindication(self):
        result = self.arena.run_battle("healthcare_contraindication", enable_self_healing=False)
        assert result["unprotected"]["status"] == "COMPROMISED"
        assert result["protected"]["status"] == "ATTACK_INTERCEPTED"
        assert result["protected"]["outcome"] == "blocked"

    def test_battle_code_exec_rce(self):
        result = self.arena.run_battle("code_exec_rce", enable_self_healing=False)
        assert result["unprotected"]["status"] == "COMPROMISED"
        assert result["protected"]["status"] == "ATTACK_INTERCEPTED"
        assert result["protected"]["outcome"] == "blocked"

    def test_battle_velocity_siphon(self):
        result = self.arena.run_battle("velocity_siphon", enable_self_healing=False)
        assert result["unprotected"]["status"] == "COMPROMISED"
        assert result["protected"]["status"] == "ATTACK_INTERCEPTED"
        assert result["protected"]["outcome"] == "blocked"

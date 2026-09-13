"""
Phase 4 tests — Cross-Domain Extension & Compliance.

Covers:
  - 4b. Field-Level Encryption
  - 4c. Crypto-Shredding
  - 4d. Asynchronous Semantic Path
"""

import pytest
from cryptography.fernet import Fernet
from verifiable_observability.storage.db import TrajectoryStore, create_db_engine
from verifiable_observability.storage.models import (
    Trajectory,
    Task,
    Domain,
    TrajectoryOutcome,
    StrategyProfile,
    RiskTier,
    Decision,
    Action,
    Turn
)
from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.core.rule_bank import RuleBank
from verifiable_observability.core.constraint_monitor import StubCCM
from verifiable_observability.agent.adapter import AgentAdapterBase

# Mock Agent Adapter for testing Orchestrator
class MockAgentAdapter(AgentAdapterBase):
    def generate(self, system_prompt, conversation, task):
        from verifiable_observability.agent.adapter import AgentResponse
        return AgentResponse(
            reasoning="This is a test reasoning.",
            tool_name=None,
            tool_parameters={},
            raw_text="Test response",
            is_final=True
        )

def _make_trajectory(traj_id="test-enc-traj"):
    task = Task(domain=Domain.FINANCE, description="Test task")
    traj = Trajectory(task=task, agent_backend="test", model_name="test")
    traj.trajectory_id = traj_id
    traj.outcome = TrajectoryOutcome.COMPLETED
    return traj

class TestFieldLevelEncryption:
    def test_save_and_load_encrypted_trajectory(self):
        engine = create_db_engine(":memory:")
        store = TrajectoryStore(engine)
        
        traj = _make_trajectory()
        store.save(traj)
        
        loaded = store.load(traj.trajectory_id)
        assert loaded is not None
        assert loaded.trajectory_id == traj.trajectory_id
        
        # Verify the raw DB row is encrypted
        with engine.connect() as conn:
            from verifiable_observability.storage.db import trajectories_table
            row = conn.execute(trajectories_table.select().where(trajectories_table.c.trajectory_id == traj.trajectory_id)).fetchone()
            
            # The data should start with 'gAAAAAB' which is Fernet prefix
            assert row.data.startswith("gAAAAA")

    def test_crypto_shredding(self):
        engine = create_db_engine(":memory:")
        store = TrajectoryStore(engine)
        
        traj = _make_trajectory()
        store.save(traj)
        
        # Shred
        assert store.crypto_shred(traj.trajectory_id) is True
        
        # Subsequent load should fail
        with pytest.raises(ValueError, match="Crypto-shredded"):
            store.load(traj.trajectory_id)
            
        # Double shred returns False
        assert store.crypto_shred(traj.trajectory_id) is False

class TestAsynchronousSemanticPath:
    def test_orchestrator_async_path_for_low_risk(self):
        engine = create_db_engine(":memory:")
        store = TrajectoryStore(engine)
        
        from verifiable_observability.storage.db import RuleStore
        rule_store = RuleStore(engine)
        orchestrator = Orchestrator(
            strategy_profiler=StrategyProfiler(),
            rule_bank=RuleBank(rule_store=rule_store),
            ccm=StubCCM(),
            agent_adapter=MockAgentAdapter(),
            trajectory_store=store,
            max_turns=1
        )
        
        # A task that is considered LOW risk
        task = Task(domain=Domain.FINANCE, description="Just checking my balance")
        traj = orchestrator.run(task)
        
        # It should complete and have turns
        assert traj.outcome == TrajectoryOutcome.COMPLETED
        assert len(traj.turns) == 1
        # Async semantic path shouldn't crash the orchestrator
        assert len(traj.turns[0].rule_checks) == 1

"""
Tests for Dashboard API Endpoints including Red-Team Arena and Self-Healing serialization.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from verifiable_observability.dashboard import app, _extract_timeline
from verifiable_observability.storage.models import (
    Trajectory,
    TrajectoryOutcome,
    Turn,
    Decision,
    Action,
    ConstraintCheckResult,
    ComplianceDecision,
    Task,
    Domain,
)


@pytest.fixture
def client(tmp_path):
    import verifiable_observability.dashboard as db_mod
    test_db = str(tmp_path / "test_dash.db")
    db_mod._DB_PATH = test_db
    return TestClient(app)


def test_dashboard_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Verifiable Observability" in response.text
    assert "Red-Team Arena" in response.text


def test_dashboard_api_data_empty(client):
    response = client.get("/api/data")
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data
    assert "details" in data


def test_redteam_scenarios_endpoint(client):
    response = client.get("/api/redteam/scenarios")
    assert response.status_code == 200
    scenarios = response.json()
    assert len(scenarios) >= 4
    scenario_ids = [s["scenario_id"] for s in scenarios]
    assert "finance_siphon" in scenario_ids
    assert "healthcare_contraindication" in scenario_ids
    assert "code_exec_rce" in scenario_ids
    assert "velocity_siphon" in scenario_ids


def test_redteam_battle_endpoint_intercept(client):
    payload = {
        "scenario_id": "finance_siphon",
        "enable_self_healing": False,
    }
    response = client.post("/api/redteam/battle", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["unprotected"]["status"] == "COMPROMISED"
    assert data["protected"]["status"] == "ATTACK_INTERCEPTED"
    assert data["protected"]["self_healed"] is False
    assert data["verdict"]["attack_neutralized"] is True


def test_redteam_battle_endpoint_self_healing(client):
    payload = {
        "scenario_id": "finance_siphon",
        "enable_self_healing": True,
    }
    response = client.post("/api/redteam/battle", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["unprotected"]["status"] == "COMPROMISED"
    assert data["protected"]["status"] == "SELF_HEALED"
    assert data["protected"]["self_healed"] is True
    assert data["verdict"]["attack_neutralized"] is True


def test_extract_timeline_with_self_healing():
    traj = Trajectory(
        task=Task(domain=Domain.FINANCE, description="Transfer $75,000"),
        outcome=TrajectoryOutcome.COMPLETED,
        self_healed=True,
        remediation_count=1,
        turns=[
            Turn(
                turn_index=1,
                decisions=[Decision(turn_index=1, reasoning="Attempt transfer")],
                actions=[Action(tool_name="execute_transfer", parameters={"amount": 75000})],
                constraint_checks=[
                    ConstraintCheckResult(
                        action_id="act-1",
                        decision=ComplianceDecision.BLOCK,
                        details="Amount exceeds $50k limit",
                    )
                ],
                intervention_prompt="COUNTERFACTUAL REMEDIATION GUIDANCE: Reduce amount to <= $50,000",
            ),
            Turn(
                turn_index=2,
                decisions=[Decision(turn_index=2, reasoning="Self-corrected to $50,000")],
                actions=[Action(tool_name="execute_transfer", parameters={"amount": 50000})],
                constraint_checks=[
                    ConstraintCheckResult(
                        action_id="act-2",
                        decision=ComplianceDecision.ALLOW,
                    )
                ],
            ),
        ],
    )

    timeline = _extract_timeline(traj)
    events = [item["event"] for item in timeline]
    assert "USER PROMPT" in events
    assert "REMEDIATION GUIDANCE" in events
    assert "RECOVERED (SELF-HEALED)" in events
    guidance_item = next(it for it in timeline if it["event"] == "REMEDIATION GUIDANCE")
    assert "COUNTERFACTUAL" in guidance_item["desc"]


def test_overview_endpoint(client):
    response = client.get("/api/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "OPERATIONAL"
    assert "total_trajectories" in data
    assert "active_domains" in data
    assert "finance" in data["active_domains"]


def test_policies_endpoint(client):
    response = client.get("/api/policies")
    assert response.status_code == 200
    data = response.json()
    assert "domains" in data
    assert "finance" in data["domains"]
    assert "healthcare" in data["domains"]
    assert "code_execution" in data["domains"]
    assert "ecommerce" in data["domains"]
    fin = data["domains"]["finance"]
    assert "FinanceCCM" in fin["active_ccm"]
    assert len(fin["rules"]) >= 4


def test_audit_verify_and_certificate_endpoints(client):
    import verifiable_observability.dashboard as db_mod
    from verifiable_observability.core.execution_ticket import TicketIssuer
    from verifiable_observability.storage.db import create_db_engine, TrajectoryStore

    engine = create_db_engine(db_mod._DB_PATH)
    store = TrajectoryStore(engine)

    issuer = TicketIssuer(secret=b"vo-master-audit-signing-secret")
    traj = Trajectory(
        task=Task(domain=Domain.FINANCE, description="Audit test"),
        outcome=TrajectoryOutcome.COMPLETED,
    )
    action0 = Action(
        action_id="act-0",
        tool_name="execute_transfer",
        parameters={"amount_usd": 100},
    )
    ticket0 = issuer.issue(action0, traj.trajectory_id, sequence_no=0)

    traj.turns.append(
        Turn(
            turn_index=0,
            decisions=[Decision(turn_index=0, reasoning="Transfer check", intended_action=action0)],
            actions=[action0],
            constraint_checks=[
                ConstraintCheckResult(
                    action_id=action0.action_id,
                    decision=ComplianceDecision.ALLOW,
                    execution_ticket=ticket0,
                )
            ],
        )
    )
    store.save(traj)

    # 1. Verify endpoint
    verify_res = client.get(f"/api/audit/verify/{traj.trajectory_id}")
    assert verify_res.status_code == 200
    verify_data = verify_res.json()
    assert verify_data["trajectory_id"] == traj.trajectory_id
    assert "VERIFIED" in verify_data["status"]
    assert verify_data["merkle_root"]
    assert verify_data["hash_chain_valid"] is True

    # 2. Certificate endpoint
    cert_res = client.get(f"/api/audit/certificate/{traj.trajectory_id}")
    assert cert_res.status_code == 200
    cert_data = cert_res.json()
    assert cert_data["trajectory_id"] == traj.trajectory_id
    assert cert_data["signature"]
    assert cert_data["merkle_root"] == verify_data["merkle_root"]


def test_audit_verify_not_found(client):
    response = client.get("/api/audit/verify/non-existent-id")
    assert response.status_code == 404


def test_pareto_endpoint(client):
    response = client.get("/api/pareto")
    assert response.status_code == 200
    data = response.json()
    assert "auc_pareto" in data
    assert "frontier" in data
    assert "all_points" in data
    assert len(data["frontier"]) >= 1

"""
Verifiable Observability — Quickstart Example.

Demonstrates end-to-end real-time behavioral verification for an agent:
  1. Strategy Profiler classifies task structure and risk tier.
  2. Rule Bank maps reasoning steps to verified Standard Operating Procedures (RCR).
  3. Constraint Compliance Monitor (CCM) enforces safety limits before tool dispatch (CCR).
  4. Cryptographic Audit Verifier generates a tamper-evident Compliance Certificate.

Run:
    python examples/quickstart.py
"""

import logging
from rich.console import Console
from rich.panel import Panel

import verifiable_observability as vo
from verifiable_observability.agent.adapter import AgentResponse, ScriptedAgentAdapter
from verifiable_observability.core.constraint_monitor import FinanceCCM
from verifiable_observability.core.execution_ticket import TicketIssuer, TicketValidator
from verifiable_observability.simulation.domains.finance.seed_rules import (
    load_seed_rules_into_bank as load_finance_rules,
)

logging.basicConfig(level=logging.WARNING)
console = Console()


def run_quickstart():
    console.rule("[bold cyan]Verifiable Observability — Quickstart[/bold cyan]")

    # 1. Setup in-memory stores and seed domain SOP rules
    engine = vo.create_db_engine(":memory:")
    traj_store = vo.TrajectoryStore(engine)
    rule_store = vo.RuleStore(engine)
    rule_bank = vo.RuleBank(rule_store)
    load_finance_rules(rule_bank, auto_verify=True)
    console.print(f"[green]✓[/green] Rule Bank seeded with {len(rule_bank.query())} verified rules.")

    # 2. Setup cryptographic ticket issuer & validator (TOCTOU protection)
    secret = b"quickstart-audit-secret-key-32b!"
    issuer = TicketIssuer(secret=secret)
    validator = TicketValidator(secret=secret)

    # 3. Setup Constraint Compliance Monitor (CCM) with ticket generation
    ccm = FinanceCCM(ticket_issuer=issuer)
    profiler = vo.StrategyProfiler()

    # 4. Scenario A: Compliant Agent Trajectory
    console.rule("[bold green]Scenario A: Compliant Workflow[/bold green]")
    compliant_responses = [
        AgentResponse(
            reasoning="Verifying account balance before initiating routine transfer.",
            tool_name="verify_balance",
            tool_parameters={"account_id": "ACC-001"},
        ),
        AgentResponse(
            reasoning="Balance verified. Initiating standard transfer of $1,500.",
            tool_name="execute_transfer",
            tool_parameters={
                "source_account": "ACC-001",
                "destination_account": "ACC-002",
                "amount": 1500.0,
            },
            is_final=True,
        ),
    ]

    task_a = vo.Task(
        description="Transfer $1,500 from ACC-001 to ACC-002.",
        domain=vo.Domain.FINANCE,
    )

    orchestrator_a = vo.Orchestrator(
        strategy_profiler=profiler,
        rule_bank=rule_bank,
        ccm=ccm,
        agent_adapter=ScriptedAgentAdapter(compliant_responses),
        trajectory_store=traj_store,
        ticket_validator=validator,
    )

    traj_a = orchestrator_a.run(task_a)
    console.print(f"Task: [bold]{task_a.description}[/bold]")
    console.print(f"Outcome: [bold green]{traj_a.outcome.value}[/bold green] | Turns: {len(traj_a.turns)}")

    # 5. Scenario B: High-Risk Violation Intercepted by CCM
    console.rule("[bold red]Scenario B: Unauthorized High-Value Transfer Blocked[/bold red]")
    unsafe_responses = [
        AgentResponse(
            reasoning="Directly executing high-value wire transfer without secondary approval.",
            tool_name="execute_transfer",
            tool_parameters={
                "source_account": "ACC-001",
                "destination_account": "ACC-999",
                "amount_usd": 600000.0,  # Exceeds the $500k unapproved transfer limit
            },
        ),
    ]

    task_b = vo.Task(
        description="Wire $600,000 to offshore account ACC-999 immediately.",
        domain=vo.Domain.FINANCE,
    )

    orchestrator_b = vo.Orchestrator(
        strategy_profiler=profiler,
        rule_bank=rule_bank,
        ccm=ccm,
        agent_adapter=ScriptedAgentAdapter(unsafe_responses),
        trajectory_store=traj_store,
        ticket_validator=validator,
    )

    traj_b = orchestrator_b.run(task_b)
    console.print(f"Task: [bold]{task_b.description}[/bold]")
    console.print(f"Outcome: [bold red]{traj_b.outcome.value}[/bold red]")
    console.print(f"Firewall Reason: [yellow]{traj_b.failure_reason}[/yellow]")

    # 6. Cryptographic Audit Verification & Compliance Certificate
    console.rule("[bold cyan]Cryptographic Audit & Certificate Generation[/bold cyan]")
    auditor = vo.AuditVerifier(signing_secret=secret)
    cert = auditor.generate_compliance_certificate(traj_a)
    is_valid = auditor.verify_certificate(cert)

    console.print(f"Certificate ID: [bold]{cert.certificate_id}[/bold]")
    console.print(f"Merkle Root:    [bold green]{cert.merkle_root}[/bold green]")
    console.print(f"Chain Head:     [bold cyan]{cert.chain_head_hash}[/bold cyan]")
    console.print(f"Authenticity:   {'[green]VERIFIED (Valid HMAC)[/green]' if is_valid else '[red]FAILED[/red]'}")

    console.print(Panel(cert.to_json(), title="Verifiable Compliance Certificate (VCC)", border_style="green"))


if __name__ == "__main__":
    run_quickstart()

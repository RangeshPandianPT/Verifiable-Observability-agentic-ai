"""
Verifiable Observability — CLI entry point.

Commands:
    demo              Run the Phase 0 smoke test end-to-end
    run               Run a real LLM agent trajectory (Phase 4)
    rulebank list     List rules in the Rule Bank
    rulebank add      Add a rule from a JSON file
    rulebank verify   Promote a pending rule to verified
    rulebank show     Show a single rule in detail
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from verifiable_observability.agent.adapter import AgentResponse, ScriptedAgentAdapter
from verifiable_observability.core.constraint_monitor import StubCCM
from verifiable_observability.core.metrics import BasicMetricsEngine
from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.core.rule_bank import RuleBank, StubRuleBank
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.simulation.domains.finance.seed_rules import (
    load_seed_rules_into_bank,
)
from verifiable_observability.storage.db import (
    RuleStore,
    TrajectoryStore,
    create_db_engine,
)
from verifiable_observability.storage.models import Domain, Rule, Task

app = typer.Typer(
    name="vo",
    help="Verifiable Observability CLI",
    rich_markup_mode="rich",
)

rulebank_app = typer.Typer(help="Rule Bank management commands")
app.add_typer(rulebank_app, name="rulebank")

console = Console()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_engine(db_path: str = "verifiable_observability.db"):
    return create_db_engine(db_path)


# ---------------------------------------------------------------------------
# demo command
# ---------------------------------------------------------------------------


@app.command()
def demo(
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
):
    """
    Run the Phase 0 smoke test end-to-end.

    Creates a fake 2-turn agent session, persists the trajectory to SQLite,
    reads it back, and prints a summary.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    console.rule("[bold cyan]Verifiable Observability — Smoke Demo[/]")

    engine = _get_engine(db)
    traj_store = TrajectoryStore(engine)

    # Scripted agent: 2 turns
    adapter = ScriptedAgentAdapter([
        AgentResponse(
            reasoning=(
                "I need to verify the account balance before initiating the transfer. "
                "I will call get_account_balance for account ACC-001."
            ),
            tool_name="get_account_balance",
            tool_parameters={"account_id": "ACC-001"},
            raw_text="[Turn 1] Checking balance...",
            is_final=False,
        ),
        AgentResponse(
            reasoning=(
                "Balance confirmed: $5,200 available. The transfer amount is $1,000, "
                "which is below $10,000, so it can be auto-approved. "
                "Executing transfer to ACC-002."
            ),
            tool_name="execute_transfer",
            tool_parameters={"from": "ACC-001", "to": "ACC-002", "amount_usd": 1000},
            raw_text="[Turn 2] Executing transfer...",
            is_final=True,
        ),
    ])

    orchestrator = Orchestrator(
        strategy_profiler=StrategyProfiler(),
        rule_bank=StubRuleBank(),
        ccm=StubCCM(),
        agent_adapter=adapter,
        trajectory_store=traj_store,
        metrics_engine=BasicMetricsEngine(),
        max_turns=10,
        agent_backend="scripted",
        model_name="scripted",
    )

    task = Task(
        domain=Domain.FINANCE,
        description="Transfer $1,000 from account ACC-001 to account ACC-002.",
        metadata={"amount_usd": 1000, "task_type": "routine_transfer"},
    )

    console.print(f"\n[bold]Task:[/] {task.description}")
    trajectory = orchestrator.run(task)

    # Pretty-print turn-by-turn
    console.print(f"\n[green]Trajectory ID:[/] {trajectory.trajectory_id}")
    console.print(f"[green]Outcome:[/]       {trajectory.outcome.value.upper()}")
    console.print(f"[green]Turns:[/]         {len(trajectory.turns)}")

    for turn in trajectory.turns:
        turn_panel = _render_turn(turn)
        console.print(turn_panel)

    # Verify persistence
    loaded = traj_store.load(trajectory.trajectory_id)
    assert loaded is not None, "Trajectory not found after save!"
    assert loaded.trajectory_id == trajectory.trajectory_id

    console.print(
        Panel(
            f"[bold green][OK] Trajectory persisted and read back successfully.[/bold green]\n"
            f"DB: [cyan]{Path(db).resolve()}[/]",
            title="Persistence Check",
        )
    )

    # Metrics summary
    engine_obj = BasicMetricsEngine()
    summary = engine_obj.trajectory_summary(trajectory)
    console.print("\n[bold]Metrics Summary:[/bold]")
    import json as _json
    console.print(_json.dumps(engine_obj.trajectory_summary(trajectory), indent=2, default=str))


def _render_turn(turn) -> Panel:
    lines = []
    for d in turn.decisions:
        lines.append(f"[yellow]Reasoning:[/] {d.reasoning[:120]}...")
        if d.intended_action:
            lines.append(
                f"[cyan]Action:[/] {d.intended_action.tool_name}("
                f"{json.dumps(d.intended_action.parameters)})"
            )
    for rc in turn.rule_checks:
        matched_str = "[green]MATCH[/]" if rc.matched else "[red]NO MATCH[/]"
        lines.append(
            f"[bold]RuleCheck:[/] {matched_str} | conf={rc.confidence:.2f} | method={rc.match_method}"
        )
    for cc in turn.constraint_checks:
        dec_color = {"ALLOW": "green", "BLOCK": "red", "FLAG": "yellow"}.get(
            cc.decision.value, "white"
        )
        lines.append(f"[bold]CCM:[/] [{dec_color}]{cc.decision.value}[/]")
    if turn.metrics.rcr is not None:
        rcr_str = f"{turn.metrics.rcr:.2f}"
        ccr_str = f"{turn.metrics.ccr:.2f}" if turn.metrics.ccr is not None else "N/A"
        lines.append(f"[bold]RCR:[/] {rcr_str}  [bold]CCR:[/] {ccr_str}")

    return Panel(
        "\n".join(lines),
        title=f"[bold]Turn {turn.turn_index}[/]",
        border_style="blue",
    )


# ---------------------------------------------------------------------------
# rulebank commands
# ---------------------------------------------------------------------------


@rulebank_app.command("list")
def rulebank_list(
    domain: Annotated[Optional[str], typer.Option("--domain", "-d", help="Filter by domain")] = None,
    status: Annotated[Optional[str], typer.Option("--status", "-s", help="Filter by status")] = None,
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
    seed: Annotated[bool, typer.Option("--seed", help="Load Finance seed rules first")] = False,
):
    """List rules in the Rule Bank."""
    engine = _get_engine(db)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    if seed:
        console.print("[dim]Loading Finance seed rules...[/dim]")
        load_seed_rules_into_bank(rule_bank, auto_verify=True)

    rules = rule_bank.query(domain=domain, status=status)

    if not rules:
        console.print("[yellow]No rules found.[/yellow]")
        return

    table = Table(title=f"Rule Bank ({len(rules)} rules)")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Domain")
    table.add_column("Task Type")
    table.add_column("Status")
    table.add_column("Version", justify="right")

    status_colors = {
        "verified": "green",
        "pending": "yellow",
        "deprecated": "red",
    }

    for r in rules:
        s = r.verification_status.value
        sc = status_colors.get(s, "white")
        table.add_row(
            r.rule_id[:12] + "…",
            r.name,
            r.domain.value,
            r.observation_pattern.task_type or "—",
            f"[{sc}]{s}[/{sc}]",
            str(r.version),
        )

    console.print(table)


@rulebank_app.command("verify")
def rulebank_verify(
    rule_id: Annotated[str, typer.Argument(help="Rule ID to verify")],
    verifier: Annotated[str, typer.Option("--verifier", help="Verifier identifier")] = "human",
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
):
    """Promote a pending rule to verified status."""
    engine = _get_engine(db)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    try:
        rule = rule_bank.verify_rule(rule_id, verifier=verifier)
        console.print(f"[green][OK][/green] Rule [cyan]{rule.rule_id[:16]}[/] verified by [bold]{verifier}[/]")
    except KeyError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(1)


@rulebank_app.command("show")
def rulebank_show(
    rule_id: Annotated[str, typer.Argument(help="Rule ID to display")],
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
):
    """Show a single rule in detail."""
    engine = _get_engine(db)
    rule_store = RuleStore(engine)
    rule = rule_store.load(rule_id)
    if rule is None:
        console.print(f"[red]Rule not found:[/] {rule_id}")
        raise typer.Exit(1)
    rprint(rule.model_dump())


@rulebank_app.command("add")
def rulebank_add(
    json_file: Annotated[Path, typer.Argument(help="Path to rule JSON file")],
    provenance: Annotated[str, typer.Option(help="Provenance note")] = "",
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
):
    """Add a rule from a JSON file."""
    engine = _get_engine(db)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    if not json_file.exists():
        console.print(f"[red]File not found:[/] {json_file}")
        raise typer.Exit(1)

    data = json.loads(json_file.read_text())
    rule = Rule.model_validate(data)
    stored = rule_bank.add_rule(rule, provenance=provenance)
    console.print(f"[green][OK][/green] Rule added: [cyan]{stored.rule_id}[/] - {stored.name}")


# ---------------------------------------------------------------------------
# run command — Phase 4: real LLM wiring (Ollama default, cloud optional)
# ---------------------------------------------------------------------------


@app.command()
def run(
    task_desc: Annotated[str, typer.Argument(help="Task description for the agent.")],
    backend: Annotated[
        Optional[str],
        typer.Option(
            "--backend",
            "-b",
            help="LLM backend: 'ollama' (default) | 'anthropic' | 'openai'. "
                 "Reads config.yaml if not specified.",
        ),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option("--model", "-m", help="Override model ID (sets AGENT_MODEL for this run)."),
    ] = None,
    domain: Annotated[
        str,
        typer.Option("--domain", "-d", help="Task domain: finance / healthcare / code_execution."),
    ] = "finance",
    max_turns: Annotated[
        int,
        typer.Option("--max-turns", help="Maximum turns before truncation."),
    ] = 10,
    seed_rules: Annotated[
        bool,
        typer.Option("--seed-rules", help="Load Finance seed rules into the Rule Bank."),
    ] = True,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
):
    """
    [bold]Phase 4[/bold] — Run a real LLM agent trajectory with full verification.

    Defaults to the Ollama local backend (zero API cost). Override with
    --backend anthropic or --backend openai for cloud models.

    Backend and model name are recorded on the Trajectory for cross-model
    RCR/CCR comparison in Phase 5-7 analysis.

    Example::

        vo run "Transfer $500 from ACC-001 to ACC-002"
        vo run "Transfer $500" --backend ollama --model llama3.2:3b
        vo run "Rebalance my portfolio" --backend openai --model gpt-4o --verbose
        vo run "Check account balance" --backend anthropic
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Apply model override to env so the factory picks it up
    if model:
        os.environ["AGENT_MODEL"] = model
        os.environ["VO_MODEL"] = model
        os.environ["VO_OPENAI_MODEL"] = model

    # --- Resolve domain ---
    try:
        task_domain = Domain(domain.lower())
    except ValueError:
        console.print(
            f"[red]Unknown domain:[/] {domain!r}. "
            "Choose from: finance, healthcare, code_execution"
        )
        raise typer.Exit(1)

    # --- Build adapter via factory ---
    from verifiable_observability.agent.factory import build_adapter
    from verifiable_observability.agent.ollama_adapter import OllamaUnavailableError, OllamaModelNotFoundError

    try:
        info = build_adapter(backend_override=backend)
    except (OllamaUnavailableError, OllamaModelNotFoundError) as exc:
        console.print(f"[red bold]Ollama error:[/red bold] {exc}")
        raise typer.Exit(1)
    except (ValueError, ImportError, RuntimeError) as exc:
        console.print(f"[red]Adapter error:[/] {exc}")
        raise typer.Exit(1)

    console.rule(
        f"[bold cyan]Verifiable Observability — {info.backend.upper()} / {info.model_name}[/]"
    )
    console.print(
        f"[dim]Backend:[/dim] [bold]{info.backend}[/]  "
        f"[dim]Model:[/dim] [bold]{info.model_name}[/]"
    )

    # --- Build supporting infrastructure ---
    engine = _get_engine(db)
    traj_store = TrajectoryStore(engine)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    if seed_rules and task_domain == Domain.FINANCE:
        console.print("[dim]Loading Finance seed rules...[/dim]")
        load_seed_rules_into_bank(rule_bank, auto_verify=True)

    # Import the real Finance CCM; fall back to stub if not yet implemented
    try:
        from verifiable_observability.core.constraint_monitor import FinanceCCM
        ccm = FinanceCCM()
    except (ImportError, AttributeError):
        console.print("[yellow]FinanceCCM not found — using StubCCM (no constraint checking).[/yellow]")
        ccm = StubCCM()

    orchestrator = Orchestrator(
        strategy_profiler=StrategyProfiler(),
        rule_bank=rule_bank,
        ccm=ccm,
        agent_adapter=info.adapter,
        trajectory_store=traj_store,
        metrics_engine=BasicMetricsEngine(),
        max_turns=max_turns,
        agent_backend=info.backend,
        model_name=info.model_name,
    )

    task = Task(
        domain=task_domain,
        description=task_desc,
        metadata={"task_type": "llm_run", "backend": info.backend, "model": info.model_name},
    )

    console.print(f"\n[bold]Task:[/] {task.description}")
    console.print(
        f"[dim]Domain:[/dim] {task_domain.value}  |  "
        f"[dim]Max turns:[/dim] {max_turns}\n"
    )

    try:
        trajectory = orchestrator.run(task)
    except (OllamaUnavailableError, OllamaModelNotFoundError) as exc:
        console.print(f"[red bold]Ollama error:[/red bold] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]LLM run failed:[/] {exc}")
        raise typer.Exit(1)

    # --- Display results ---
    outcome_color = {
        "completed": "green",
        "blocked": "red",
        "failed": "red",
        "truncated": "yellow",
        "in_progress": "cyan",
    }.get(trajectory.outcome.value, "white")

    console.print(f"[green]Trajectory ID:[/]  {trajectory.trajectory_id}")
    console.print(f"[green]Backend:[/]        {trajectory.agent_backend} / {trajectory.model_name}")
    console.print(
        f"[{outcome_color}]Outcome:[/{outcome_color}]        {trajectory.outcome.value.upper()}"
    )
    console.print(f"[green]Turns:[/]          {len(trajectory.turns)}")

    if trajectory.failure_reason:
        console.print(f"[red]Failure reason:[/] {trajectory.failure_reason}")

    for turn in trajectory.turns:
        console.print(_render_turn(turn))

    engine_obj = BasicMetricsEngine()
    summary = engine_obj.trajectory_summary(trajectory)
    # Inject backend provenance into the summary
    summary["agent_backend"] = trajectory.agent_backend
    summary["model_name"] = trajectory.model_name
    console.print("\n[bold]Metrics Summary:[/bold]")
    console.print(json.dumps(summary, indent=2, default=str))

    console.print(
        Panel(
            f"[bold green]Run complete.[/bold green]\n"
            f"Backend: [cyan]{trajectory.agent_backend}[/] / [cyan]{trajectory.model_name}[/]\n"
            f"DB: [cyan]{Path(db).resolve()}[/]",
            title="Done",
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()

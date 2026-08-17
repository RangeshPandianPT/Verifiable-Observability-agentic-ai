"""
Verifiable Observability — CLI entry point.

Commands:
    demo              Run the Phase 0 smoke test end-to-end
    run               Run a real LLM agent trajectory (Phase 4)
    rulebank list     List rules in the Rule Bank
    rulebank add      Add a rule from a JSON file
    rulebank verify   Promote a pending rule to verified
    rulebank show     Show a single rule in detail
    analyze           Phase 5 — compare trajectories; detect drift
    regime run        Phase 5 — run a scripted behavioral regime
    domain seed       Phase 6 — load seed rules for a domain into the Rule Bank
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
from verifiable_observability.core.constraint_monitor import StubCCM, build_ccm
from verifiable_observability.core.metrics import BasicMetricsEngine
from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.core.rule_bank import RuleBank, StubRuleBank
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.simulation.domains.finance.seed_rules import (
    load_seed_rules_into_bank as load_finance_rules,
)
from verifiable_observability.simulation.domains.healthcare.seed_rules import (
    load_seed_rules_into_bank as load_healthcare_rules,
)
from verifiable_observability.simulation.domains.code_execution.seed_rules import (
    load_seed_rules_into_bank as load_code_exec_rules,
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

analyze_app = typer.Typer(help="Phase 5 — trajectory analysis and drift detection")
app.add_typer(analyze_app, name="analyze")

regime_app = typer.Typer(help="Phase 5 — scripted behavioral regime runs")
app.add_typer(regime_app, name="regime")

domain_app = typer.Typer(help="Phase 6 — domain seed-rule management")
app.add_typer(domain_app, name="domain")

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

    if seed_rules:
        _load_seed_rules_for_domain(task_domain, rule_bank, console)

    # Build CCM for the task's domain (Phase 6: multi-domain CCM selection)
    try:
        ccm = build_ccm(task_domain.value)
    except KeyError:
        console.print("[yellow]No CCM registered for this domain — using StubCCM.[/yellow]")
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
# analyze commands — Phase 5
# ---------------------------------------------------------------------------


@analyze_app.command("trajectories")
def analyze_trajectories(
    domain: Annotated[
        Optional[str],
        typer.Option("--domain", "-d", help="Filter by domain."),
    ] = None,
    outcome: Annotated[
        Optional[str],
        typer.Option("--outcome", "-o", help="Filter by outcome (completed/blocked/truncated)."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Max trajectories to load."),
    ] = 50,
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
    drift_only: Annotated[
        bool,
        typer.Option("--drift-only", help="Show only trajectories with drift detected."),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
):
    """
    [bold]Phase 5[/bold] — Compare stored trajectories and flag behavioural drift.

    Loads trajectories from the DB and prints a comparison table with
    per-trajectory RCR, CCR, trend direction, and drift flag.

    Example::

        vo analyze trajectories
        vo analyze trajectories --drift-only
        vo analyze trajectories --domain finance --outcome blocked
        vo analyze trajectories -n 20 --verbose
    """
    from verifiable_observability.core.metrics import BasicMetricsEngine
    from verifiable_observability.storage.db import TrajectoryStore, create_db_engine

    engine = _get_engine(db)
    traj_store = TrajectoryStore(engine)
    metrics_engine = BasicMetricsEngine()

    console.rule("[bold cyan]Verifiable Observability — Trajectory Analysis[/]")

    summaries = traj_store.list_trajectories(domain=domain, outcome=outcome, limit=limit)
    if not summaries:
        console.print("[yellow]No trajectories found in the database.[/yellow]")
        raise typer.Exit(0)

    # Load full trajectory objects for drift analysis
    trajectories = []
    for s in summaries:
        t = traj_store.load(s["trajectory_id"])
        if t is not None:
            trajectories.append(t)

    rows = metrics_engine.compare_trajectories(trajectories)

    if drift_only:
        rows = [r for r in rows if r["drift"] != "OK"]
        if not rows:
            console.print("[green]No drift detected in any trajectory.[/green]")
            raise typer.Exit(0)

    table = Table(
        title=f"Trajectory Analysis ({len(rows)} trajectories)",
        show_lines=False,
    )
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Backend", style="cyan")
    table.add_column("Model", style="cyan")
    table.add_column("Regime")
    table.add_column("Turns", justify="right")
    table.add_column("Outcome")
    table.add_column("Avg RCR", justify="right")
    table.add_column("Avg CCR", justify="right")
    table.add_column("RCR Trend")
    table.add_column("CCR Trend")
    table.add_column("Drift")

    outcome_colors = {
        "completed": "green",
        "blocked": "red",
        "truncated": "yellow",
        "failed": "red",
        "in_progress": "cyan",
    }
    trend_colors = {
        "improving": "green",
        "stable": "white",
        "degrading": "red",
        "insufficient_data": "dim",
    }

    for r in rows:
        oc = outcome_colors.get(r["outcome"], "white")
        rcr_c = trend_colors.get(r["rcr_trend"], "white")
        ccr_c = trend_colors.get(r["ccr_trend"], "white")
        drift_style = "bold red" if r["drift"] != "OK" else "green"

        table.add_row(
            r["trajectory_id"],
            r["backend"],
            r["model"],
            r["regime"],
            str(r["turns"]),
            f"[{oc}]{r['outcome']}[/{oc}]",
            r["avg_rcr"],
            r["avg_ccr"],
            f"[{rcr_c}]{r['rcr_trend']}[/{rcr_c}]",
            f"[{ccr_c}]{r['ccr_trend']}[/{ccr_c}]",
            f"[{drift_style}]{r['drift']}[/{drift_style}]",
        )

    console.print(table)

    if verbose:
        # Print per-trajectory drift report in JSON
        console.print("\n[bold]Per-trajectory Drift Reports:[/bold]")
        for traj in trajectories:
            report = metrics_engine.detect_drift(traj)
            console.print(json.dumps(report.to_dict(), indent=2, default=str))



# ---------------------------------------------------------------------------
# regime commands — Phase 5
# ---------------------------------------------------------------------------


@regime_app.command("run")
def regime_run(
    regime_name: Annotated[
        str,
        typer.Argument(
            help="Regime to run: compliant | mild_drift | adversarial_injection | tool_failure_drift"
        ),
    ],
    domain: Annotated[
        str,
        typer.Option("--domain", "-d", help="Task domain."),
    ] = "finance",
    seed_rules: Annotated[
        bool,
        typer.Option("--seed-rules", help="Load Finance seed rules into Rule Bank."),
    ] = True,
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
):
    """
    [bold]Phase 5[/bold] — Run a scripted behavioral regime through the full verification stack.

    Each regime produces a scripted trajectory with a known behavioral signature,
    letting you validate that the Rule Bank and CCM respond as expected.

    Regimes::

        compliant              All rules matched, all constraints satisfied.
        mild_drift             Skips pre-checks; rule misses without hard violations.
        adversarial_injection  Large unauthorized transfer triggers CCM BLOCK.
        tool_failure_drift     Tool errors cause declining RCR over turns.

    Example::

        vo regime run compliant
        vo regime run adversarial_injection --verbose
        vo regime run tool_failure_drift --domain finance
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from verifiable_observability.simulation.regimes import RegimeType, build_regime
    from verifiable_observability.core.metrics import BasicMetricsEngine
    from verifiable_observability.storage.db import RuleStore, TrajectoryStore, create_db_engine

    # --- Resolve regime ---
    try:
        regime = build_regime(regime_name)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    # --- Resolve domain ---
    try:
        task_domain = Domain(domain.lower())
    except ValueError:
        console.print(
            f"[red]Unknown domain:[/] {domain!r}. "
            "Choose from: finance, healthcare, code_execution"
        )
        raise typer.Exit(1)

    console.rule(
        f"[bold cyan]Verifiable Observability — Regime: {regime.regime_type.value.upper()}[/]"
    )
    console.print(f"[dim]{regime.description}[/dim]\n")

    # --- Infrastructure ---
    engine = _get_engine(db)
    traj_store = TrajectoryStore(engine)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    if seed_rules:
        _load_seed_rules_for_domain(task_domain, rule_bank, console)

    try:
        ccm = build_ccm(task_domain.value)
    except KeyError:
        console.print("[yellow]No CCM registered for this domain — using StubCCM.[/yellow]")
        ccm = StubCCM()

    task_description = (
        f"[Regime: {regime.regime_type.value}] Transfer funds from ACC-001 to ACC-002."
    )
    adapter = regime.build_adapter(task_description=task_description)

    orchestrator = Orchestrator(
        strategy_profiler=StrategyProfiler(),
        rule_bank=rule_bank,
        ccm=ccm,
        agent_adapter=adapter,
        trajectory_store=traj_store,
        metrics_engine=BasicMetricsEngine(),
        max_turns=10,
        agent_backend="scripted",
        model_name=f"regime:{regime.regime_type.value}",
    )

    task = Task(
        domain=task_domain,
        description=task_description,
        metadata={
            "regime": regime.regime_type.value,
            "task_type": "routine_transfer",
        },
    )

    console.print(f"[bold]Task:[/] {task_description}")
    trajectory = orchestrator.run(task)

    # --- Results ---
    outcome_color = {
        "completed": "green",
        "blocked": "red",
        "failed": "red",
        "truncated": "yellow",
        "in_progress": "cyan",
    }.get(trajectory.outcome.value, "white")

    console.print(f"\n[green]Trajectory ID:[/] {trajectory.trajectory_id}")
    console.print(
        f"[{outcome_color}]Outcome:[/{outcome_color}]       "
        f"{trajectory.outcome.value.upper()}"
    )
    console.print(f"[green]Turns:[/]         {len(trajectory.turns)}")

    if trajectory.failure_reason:
        console.print(f"[red]Failure reason:[/] {trajectory.failure_reason}")

    for turn in trajectory.turns:
        console.print(_render_turn(turn))

    # Drift analysis
    metrics_engine = BasicMetricsEngine()
    report = metrics_engine.detect_drift(trajectory, regime=regime.regime_type.value)
    summary = metrics_engine.trajectory_summary(trajectory)
    summary["regime"] = regime.regime_type.value

    console.print("\n[bold]Metrics Summary:[/bold]")
    console.print(json.dumps(summary, indent=2, default=str))

    # Drift report
    drift_color = "bold red" if report.drift_detected else "bold green"
    drift_label = "⚠ DRIFT DETECTED" if report.drift_detected else "✓ No drift detected"
    drift_info = (
        "  Reasons:\n" + "\n".join(f"  • {r}" for r in report.drift_reasons)
        if report.drift_reasons
        else ""
    )
    console.print(
        Panel(
            f"[{drift_color}]{drift_label}[/{drift_color}]\n"
            f"RCR trend: [bold]{report.rcr_trend.value}[/]  "
            f"CCR trend: [bold]{report.ccr_trend.value}[/]"
            + (f"\n{drift_info}" if drift_info else ""),
            title="Drift Analysis",
            border_style="red" if report.drift_detected else "green",
        )
    )

    # Check expectations
    expectations = regime.expectations
    expected_outcomes = expectations["expected_outcomes"]
    ok = trajectory.outcome.value in expected_outcomes
    exp_color = "green" if ok else "red"
    console.print(
        Panel(
            f"[{exp_color}]Outcome match: {ok}[/{exp_color}]\n"
            f"Expected: {expected_outcomes}  |  Got: {trajectory.outcome.value}\n"
            f"Drift expected: {expectations['drift_expected']}  "
            f"|  Detected: {report.drift_detected}",
            title="Expectation Check",
            border_style=exp_color,
        )
    )


@regime_app.command("list")
def regime_list():
    """List all available behavioral regimes."""
    from verifiable_observability.simulation.regimes import (
        ALL_REGIMES,
        REGIME_EXPECTATIONS,
        RegimeType,
        build_regime,
    )

    console.rule("[bold cyan]Behavioral Regimes[/]")
    table = Table(title="Available Regimes (Phase 5)")
    table.add_column("Regime", style="bold cyan")
    table.add_column("Description")
    table.add_column("Expected Outcome")
    table.add_column("Drift Expected?")

    for rt in ALL_REGIMES:
        regime = build_regime(rt)
        exp = REGIME_EXPECTATIONS[rt]
        drift_exp = "[bold red]Yes[/]" if exp["drift_expected"] else "[green]No[/]"
        table.add_row(
            rt.value,
            regime.description,
            ", ".join(exp["expected_outcomes"]),
            drift_exp,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# domain commands — Phase 6
# ---------------------------------------------------------------------------


_SEED_LOADERS = {
    Domain.FINANCE: (load_finance_rules, "finance_seed_v1"),
    Domain.HEALTHCARE: (load_healthcare_rules, "healthcare_seed_v1"),
    Domain.CODE_EXECUTION: (load_code_exec_rules, "code_execution_seed_v1"),
}


def _load_seed_rules_for_domain(
    domain: Domain,
    rule_bank: RuleBank,
    con: Console,
) -> int:
    """
    Load seed rules for the given domain into the rule bank.

    Returns the count of rules loaded.
    """
    entry = _SEED_LOADERS.get(domain)
    if entry is None:
        con.print(f"[yellow]No seed rules defined for domain '{domain.value}'.[/yellow]")
        return 0
    loader, label = entry
    con.print(f"[dim]Loading {domain.value} seed rules ({label})...[/dim]")
    loaded = loader(rule_bank, auto_verify=True)
    return len(loaded)


@domain_app.command("seed")
def domain_seed(
    domain_name: Annotated[
        str,
        typer.Argument(help="Domain to seed: finance | healthcare | code_execution"),
    ],
    db: Annotated[str, typer.Option(help="Path to SQLite DB")] = "verifiable_observability.db",
    verify: Annotated[
        bool,
        typer.Option("--verify/--no-verify", help="Auto-verify all loaded rules."),
    ] = True,
):
    """
    [bold]Phase 6[/bold] — Load seed rules for a domain into the Rule Bank.

    Idempotent: duplicate rules are silently skipped by the Rule Bank.

    Example::

        vo domain seed finance
        vo domain seed healthcare
        vo domain seed code_execution --no-verify
    """
    try:
        dom = Domain(domain_name.lower())
    except ValueError:
        console.print(
            f"[red]Unknown domain:[/] {domain_name!r}. "
            "Choose from: finance, healthcare, code_execution"
        )
        raise typer.Exit(1)

    engine = _get_engine(db)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    entry = _SEED_LOADERS.get(dom)
    if entry is None:
        console.print(f"[red]No seed loader found for domain:[/] {dom.value}")
        raise typer.Exit(1)

    loader, label = entry
    console.rule(f"[bold cyan]Loading seed rules — {dom.value.upper()}[/]")
    rules = loader(rule_bank, auto_verify=verify)

    table = Table(title=f"{dom.value.title()} Seed Rules ({len(rules)} loaded)")
    table.add_column("Rule ID", style="cyan")
    table.add_column("Name")
    table.add_column("Task Type")
    table.add_column("Status")

    status_colors = {
        "verified": "green",
        "pending": "yellow",
        "deprecated": "red",
    }
    for r in rules:
        s = r.verification_status.value
        sc = status_colors.get(s, "white")
        table.add_row(
            r.rule_id,
            r.name,
            r.observation_pattern.task_type or "—",
            f"[{sc}]{s}[/{sc}]",
        )

    console.print(table)
    console.print(
        Panel(
            f"[bold green][OK] {len(rules)} rules seeded into '{dom.value}' domain.[/bold green]\n"
            f"Auto-verified: [bold]{'Yes' if verify else 'No'}[/]\n"
            f"DB: [cyan]{Path(db).resolve()}[/]",
            title="Seed Complete",
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()

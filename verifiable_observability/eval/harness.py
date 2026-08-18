import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import yaml
from rich.console import Console

from verifiable_observability.agent.factory import build_adapter
from verifiable_observability.core.constraint_monitor import build_ccm, StubCCM
from verifiable_observability.core.metrics import BasicMetricsEngine
from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.core.rule_bank import RuleBank
from verifiable_observability.core.strategy_profiler import StrategyProfiler
from verifiable_observability.simulation.regimes import build_regime
from verifiable_observability.storage.db import create_db_engine, TrajectoryStore, RuleStore
from verifiable_observability.storage.models import Domain, Task
from verifiable_observability.cli.main import _load_seed_rules_for_domain


class EvalHarness:
    """
    Phase 7 — Evaluation Harness.
    Reads experiment config and executes the Cartesian product of the sweep matrix.
    """

    def __init__(self, config_path: str = "config.yaml", db_path: str = "verifiable_observability.db", console: Optional[Console] = None):
        self.config_path = config_path
        self.db_path = db_path
        self.console = console or Console()
        self.config = self._load_config()
        self.exp_config = self.config.get("experiment", {})

    def _load_config(self) -> Dict[str, Any]:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def run_sweep(self) -> str:
        """Run the experiment sweep matrix and return path to the report."""
        name = self.exp_config.get("name", "experiment")
        domains = self.exp_config.get("domains", ["finance"])
        regimes = self.exp_config.get("regimes", [])
        backends = self.exp_config.get("agent_backends", [])
        n_traj = self.exp_config.get("n_trajectories_per_cell", 1)
        output_dir = Path(self.exp_config.get("output_dir", "experiment_results/"))
        output_dir.mkdir(parents=True, exist_ok=True)

        engine = create_db_engine(self.db_path)
        traj_store = TrajectoryStore(engine)
        rule_store = RuleStore(engine)

        total_runs = len(domains) * (len(regimes) + len(backends)) * n_traj
        self.console.print(f"[bold cyan]Starting Sweep: {name}[/]")
        self.console.print(f"Total trajectories to run: {total_runs}")

        results = []

        for domain_name in domains:
            try:
                domain = Domain(domain_name.lower())
            except ValueError:
                self.console.print(f"[red]Unknown domain {domain_name}, skipping.[/red]")
                continue

            rule_bank = RuleBank(rule_store)
            _load_seed_rules_for_domain(domain, rule_bank, self.console)

            try:
                ccm = build_ccm(domain.value)
            except KeyError:
                ccm = StubCCM()

            # 1. Run Regimes
            for regime_name in regimes:
                try:
                    regime = build_regime(regime_name)
                except ValueError as e:
                    self.console.print(f"[red]Unknown regime {regime_name}, skipping.[/red]")
                    continue

                for i in range(n_traj):
                    self.console.print(f"[dim]Run {i+1}/{n_traj}: Domain={domain.value}, Regime={regime_name}[/dim]")
                    task_desc = f"[Regime: {regime.regime_type.value}] Perform operations in {domain.value}."
                    adapter = regime.build_adapter(task_desc)

                    orchestrator = Orchestrator(
                        strategy_profiler=StrategyProfiler(),
                        rule_bank=rule_bank,
                        ccm=ccm,
                        agent_adapter=adapter,
                        trajectory_store=traj_store,
                        metrics_engine=BasicMetricsEngine(),
                        max_turns=10,
                        agent_backend="scripted",
                        model_name=f"regime:{regime_name}",
                    )

                    task = Task(
                        domain=domain,
                        description=task_desc,
                        metadata={"task_type": "eval_sweep", "regime": regime_name}
                    )

                    traj = orchestrator.run(task)
                    metrics = BasicMetricsEngine().trajectory_summary(traj)
                    results.append({
                        "domain": domain.value,
                        "type": "regime",
                        "regime": regime_name,
                        "trajectory_id": traj.trajectory_id,
                        "outcome": traj.outcome.value,
                        "avg_rcr": metrics.get("avg_rcr"),
                        "avg_ccr": metrics.get("avg_ccr"),
                    })

            # 2. Run LLM Backends
            for backend_info in backends:
                backend = backend_info.get("backend")
                model = backend_info.get("model")
                for i in range(n_traj):
                    self.console.print(f"[dim]Run {i+1}/{n_traj}: Domain={domain.value}, Backend={backend}, Model={model}[/dim]")
                    try:
                        import os
                        if model:
                            os.environ["AGENT_MODEL"] = model
                        adapter_info = build_adapter(backend_override=backend)
                    except Exception as e:
                        self.console.print(f"[red]Failed to load adapter {backend}: {e}[/red]")
                        continue

                    task_desc = f"Execute standard {domain.value} tasks."
                    orchestrator = Orchestrator(
                        strategy_profiler=StrategyProfiler(),
                        rule_bank=rule_bank,
                        ccm=ccm,
                        agent_adapter=adapter_info.adapter,
                        trajectory_store=traj_store,
                        metrics_engine=BasicMetricsEngine(),
                        max_turns=10,
                        agent_backend=adapter_info.backend,
                        model_name=adapter_info.model_name,
                    )

                    task = Task(
                        domain=domain,
                        description=task_desc,
                        metadata={"task_type": "eval_sweep"}
                    )

                    try:
                        traj = orchestrator.run(task)
                        metrics = BasicMetricsEngine().trajectory_summary(traj)
                        results.append({
                            "domain": domain.value,
                            "type": "llm",
                            "backend": adapter_info.backend,
                            "model": adapter_info.model_name,
                            "trajectory_id": traj.trajectory_id,
                            "outcome": traj.outcome.value,
                            "avg_rcr": metrics.get("avg_rcr"),
                            "avg_ccr": metrics.get("avg_ccr"),
                        })
                    except Exception as e:
                        self.console.print(f"[red]Run failed: {e}[/red]")

        # Generate report
        report_path = output_dir / f"{name}_results.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        
        self.console.print(f"[bold green]Sweep complete! Report saved to {report_path}[/bold green]")
        return str(report_path)

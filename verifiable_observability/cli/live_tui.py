import time
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.align import Align

from verifiable_observability.core.orchestrator import OrchestratorCallback

class LiveTUI(OrchestratorCallback):
    def __init__(self):
        self.layout = self._make_layout()
        self.live = Live(self.layout, refresh_per_second=10, screen=True)
        self.agent_log = []
        self.rule_log = []
        self.ccm_log = []
        self.metrics_text = "Waiting for metrics..."

    def _make_layout(self):
        layout = Layout(name="root")
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )
        layout["main"].split_row(
            Layout(name="agent", ratio=2),
            Layout(name="verification", ratio=1)
        )
        layout["verification"].split_column(
            Layout(name="rulebank"),
            Layout(name="ccm")
        )
        return layout

    def start(self):
        self.live.start()
        
    def stop(self):
        self.live.stop()

    def update_ui(self):
        agent_txt = Text.from_markup("\n".join(self.agent_log[-20:]))
        self.layout["agent"].update(Panel(agent_txt, title="[cyan]Agent Reasoning Stream[/cyan]", border_style="cyan"))

        rule_txt = Text.from_markup("\n".join(self.rule_log[-15:]))
        self.layout["rulebank"].update(Panel(rule_txt, title="[magenta]Rule Bank (Semantic Checks)[/magenta]", border_style="magenta"))

        ccm_txt = Text.from_markup("\n".join(self.ccm_log[-15:]))
        self.layout["ccm"].update(Panel(ccm_txt, title="[yellow]Constraint Monitor (Symbolic Checks)[/yellow]", border_style="yellow"))
        
        self.layout["footer"].update(Panel(Align.center(self.metrics_text), border_style="white"))
        
    def on_trajectory_start(self, task, profile, trajectory):
        header_text = f"[bold white]Task:[/] {task.description} | [bold cyan]Domain:[/] {profile.domain.value} | [bold yellow]Risk:[/] {profile.risk_tier.value}"
        self.layout["header"].update(Panel(Align.center(header_text), style="on blue"))
        self.agent_log.append(f"[dim]Trajectory {trajectory.trajectory_id[:8]} started.[/dim]")
        self.update_ui()
        time.sleep(0.5)

    def on_input_guardrail_check(self, decision, reason):
        color = "green" if decision == "ALLOW" else "red"
        self.ccm_log.append(f"[{color}]Input Guardrail:[/{color}] {decision} ({reason})")
        self.update_ui()
        time.sleep(1)

    def on_turn_start(self, turn_index):
        self.agent_log.append(f"\n[bold white]--- Turn {turn_index} ---[/bold white]")
        self.update_ui()
        time.sleep(0.5)

    def on_agent_response(self, agent_resp):
        if agent_resp.reasoning:
            self.agent_log.append(f"[bold cyan]Reasoning:[/bold cyan] {agent_resp.reasoning}")
            self.update_ui()
            time.sleep(1.2) # Wait for human to read reasoning
        if agent_resp.tool_name:
            self.agent_log.append(f"[bold green]Action:[/bold green] {agent_resp.tool_name}({agent_resp.tool_parameters})")
            self.update_ui()
            time.sleep(0.8)

    def on_rule_check(self, rule_check):
        color = "green" if rule_check.matched else "red"
        match_str = "MATCH" if rule_check.matched else "NO MATCH"
        rule_name = rule_check.rule_name if rule_check.rule_name else "N/A"
        self.rule_log.append(f"[{color}]{match_str}[/{color}] | conf={rule_check.confidence:.2f} | rule={rule_name}")
        self.update_ui()
        time.sleep(0.8)

    def on_ccm_check(self, ccm_result):
        dec_color = {"ALLOW": "green", "BLOCK": "red", "FLAG": "yellow"}.get(ccm_result.decision.value, "white")
        self.ccm_log.append(f"[{dec_color}]{ccm_result.decision.value}[/{dec_color}] violations={len(ccm_result.violated_constraints)}")
        if ccm_result.violated_constraints:
            for v in ccm_result.violated_constraints:
                self.ccm_log.append(f"  [red]![/red] {v.details}")
        self.update_ui()
        time.sleep(1.2)

    def on_action_dispatch(self, action, result):
        self.agent_log.append(f"[dim]Tool Result: {str(result)[:50]}...[/dim]")
        self.update_ui()
        time.sleep(0.5)

    def on_turn_end(self, turn):
        metrics = turn.metrics
        if metrics:
            rcr = f"{metrics.rcr:.2f}" if metrics.rcr is not None else "N/A"
            ccr = f"{metrics.ccr:.2f}" if metrics.ccr is not None else "N/A"
            self.metrics_text = f"[bold]Current RCR (Reasoning Consistency):[/bold] [cyan]{rcr}[/cyan]  |  [bold]Current CCR (Constraint Compliance):[/bold] [cyan]{ccr}[/cyan]"
        self.update_ui()
        time.sleep(0.5)

    def on_trajectory_end(self, trajectory):
        self.agent_log.append(f"\n[bold magenta]Trajectory {trajectory.outcome.value.upper()}[/bold magenta]")
        self.update_ui()
        time.sleep(2)

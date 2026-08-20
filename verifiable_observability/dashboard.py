"""
Phase 8 — Minimal Dashboard.

Serves a live HTML table of recent trajectories pulled directly from the SQLite DB.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

from verifiable_observability.core.metrics import BasicMetricsEngine
from verifiable_observability.storage.db import TrajectoryStore, create_db_engine

# DB path resolved at startup time (set by run_dashboard before uvicorn starts)
_DB_PATH: str = "verifiable_observability.db"

app = FastAPI(title="Verifiable Observability Dashboard")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verifiable Observability Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

        :root {{
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --border-color: #e2e8f0;
            --text-main: #0f172a;
            --text-muted: #64748b;
            --text-subtle: #94a3b8;
            --primary: #4f46e5;
            --primary-hover: #4338ca;
            --primary-light: #eef2ff;
            --shadow-sm: 0 1px 3px 0 rgba(15, 23, 42, 0.05), 0 1px 2px -1px rgba(15, 23, 42, 0.03);
            --shadow-md: 0 4px 6px -1px rgba(15, 23, 42, 0.05), 0 2px 4px -2px rgba(15, 23, 42, 0.04);
        }}

        body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            min-height: 100vh;
            padding: 32px 24px;
            line-height: 1.5;
        }}

        .container {{
            max-width: 1280px;
            margin: 0 auto;
        }}

        header {{
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--card-bg);
            padding: 20px 28px;
            border-radius: 16px;
            border: 1px solid var(--border-color);
            box-shadow: var(--shadow-sm);
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}

        .brand-icon {{
            width: 42px;
            height: 42px;
            background: linear-gradient(135deg, #4f46e5, #3b82f6);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            font-size: 1.25rem;
            box-shadow: 0 4px 10px rgba(79, 70, 229, 0.25);
        }}

        .brand-title h1 {{
            font-size: 1.4rem;
            font-weight: 800;
            color: var(--text-main);
            letter-spacing: -0.02em;
        }}

        .brand-title p {{
            font-size: 0.82rem;
            color: var(--text-muted);
            font-weight: 500;
        }}

        .badge {{
            background: var(--primary-light);
            border: 1px solid #c7d2fe;
            border-radius: 9999px;
            padding: 6px 14px;
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--primary);
        }}

        .run-form-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 20px 24px;
            margin-bottom: 24px;
            box-shadow: var(--shadow-sm);
        }}

        .form-label {{
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
            margin-bottom: 10px;
            display: block;
        }}

        .run-form {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .run-form input {{
            flex: 1;
            min-width: 280px;
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 12px 18px;
            color: var(--text-main);
            font-size: 0.9rem;
            font-family: inherit;
            transition: all 0.15s ease;
        }}

        .run-form input:focus {{
            outline: none;
            border-color: #6366f1;
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
        }}

        .run-form select {{
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 12px 18px;
            color: var(--text-main);
            font-size: 0.9rem;
            font-family: inherit;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
        }}

        .run-form select:focus {{
            outline: none;
            border-color: #6366f1;
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
        }}

        .run-btn {{
            background: linear-gradient(135deg, #4f46e5, #3b82f6);
            color: #ffffff;
            border: none;
            border-radius: 10px;
            padding: 12px 24px;
            font-weight: 600;
            font-size: 0.9rem;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.15s ease;
            box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}

        .run-btn:hover {{
            background: linear-gradient(135deg, #4338ca, #2563eb);
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(79, 70, 229, 0.35);
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 18px;
            margin-bottom: 24px;
        }}

        .stat-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 20px 24px;
            box-shadow: var(--shadow-sm);
            position: relative;
            overflow: hidden;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}

        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow-md);
        }}

        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
        }}

        .stat-card.total::before {{ background: #6366f1; }}
        .stat-card.completed::before {{ background: #10b981; }}
        .stat-card.blocked::before {{ background: #f43f5e; }}
        .stat-card.drift::before {{ background: #f59e0b; }}

        .stat-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
        }}

        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 700;
        }}

        .stat-icon {{
            font-size: 1.1rem;
        }}

        .stat-value {{
            font-size: 2.25rem;
            font-weight: 800;
            letter-spacing: -0.03em;
            line-height: 1.1;
        }}

        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            box-shadow: var(--shadow-sm);
            overflow: hidden;
        }}

        .card-header {{
            padding: 20px 24px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #ffffff;
        }}

        .card-header-title {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .card-header h2 {{
            font-size: 1.05rem;
            font-weight: 700;
            color: var(--text-main);
        }}

        .count-pill {{
            background: #f1f5f9;
            color: var(--text-muted);
            font-size: 0.75rem;
            font-weight: 600;
            padding: 2px 10px;
            border-radius: 9999px;
        }}

        .refresh-btn {{
            background: #ffffff;
            color: var(--text-muted);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 0.82rem;
            font-weight: 600;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}

        .refresh-btn:hover {{
            background: #f8fafc;
            color: var(--text-main);
            border-color: #cbd5e1;
        }}

        .table-responsive {{
            width: 100%;
            overflow-x: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}

        thead th {{
            background: #f8fafc;
            padding: 14px 18px;
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
            white-space: nowrap;
        }}

        tbody tr {{
            border-bottom: 1px solid #f1f5f9;
            transition: background-color 0.15s ease;
        }}

        tbody tr:last-child {{
            border-bottom: none;
        }}

        tbody tr:hover {{
            background-color: #f8fafc;
        }}

        td {{
            padding: 14px 18px;
            font-size: 0.86rem;
            color: #334155;
            vertical-align: middle;
        }}

        .mono {{
            font-family: 'JetBrains Mono', 'Consolas', monospace;
            font-size: 0.78rem;
            background: #f1f5f9;
            color: #475569;
            padding: 3px 8px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            display: inline-block;
        }}

        /* Badges */
        .badge-completed {{ background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; border-radius: 9999px; padding: 4px 12px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; display: inline-block; }}
        .badge-blocked   {{ background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; border-radius: 9999px; padding: 4px 12px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; display: inline-block; }}
        .badge-truncated {{ background: #ffedd5; color: #c2410c; border: 1px solid #fed7aa; border-radius: 9999px; padding: 4px 12px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; display: inline-block; }}
        .badge-failed    {{ background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; border-radius: 9999px; padding: 4px 12px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; display: inline-block; }}
        .badge-default   {{ background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; border-radius: 9999px; padding: 4px 12px; font-size: 0.72rem; font-weight: 600; display: inline-block; }}

        /* Metrics & Drift */
        .drift-ok   {{ color: #16a34a; font-weight: 600; }}
        .drift-warn {{ color: #dc2626; font-weight: 700; }}

        .trend-improving {{ color: #16a34a; font-weight: 600; }}
        .trend-degrading {{ color: #dc2626; font-weight: 600; }}
        .trend-stable    {{ color: #64748b; }}
        .trend-insufficient {{ color: #94a3b8; }}

        .metric {{ font-weight: 600; font-family: 'JetBrains Mono', monospace; font-size: 0.84rem; color: #1e293b; }}

        .empty-state {{
            padding: 64px 24px;
            text-align: center;
            color: var(--text-muted);
            background: #ffffff;
        }}
        .empty-state .icon {{ font-size: 3rem; margin-bottom: 12px; }}
        .empty-state p {{ font-size: 0.92rem; color: var(--text-muted); }}
        .empty-state code {{
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            color: #334155;
            padding: 3px 8px;
            border-radius: 6px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.82rem;
        }}

        footer {{
            margin-top: 32px;
            font-size: 0.78rem;
            color: var(--text-subtle);
            text-align: center;
            padding-bottom: 16px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div class="brand-icon">⚡</div>
                <div class="brand-title">
                    <h1>Verifiable Observability</h1>
                    <p>Real-Time Agent Trajectory & Behavioral Verification Engine</p>
                </div>
            </div>
            <span class="badge">Phase 8/9 Dashboard</span>
        </header>

        <div class="run-form-card">
            <span class="form-label">Test Agent Prompt</span>
            <form class="run-form" method="POST" action="/run_task">
                <input type="text" name="prompt" placeholder="Type a prompt for the agent (e.g., 'Transfer $500 from ACC-001...')" required />
                <select name="domain">
                    <option value="finance">Finance Domain</option>
                    <option value="healthcare">Healthcare Domain</option>
                    <option value="code_execution">Code Execution Domain</option>
                </select>
                <button type="submit" class="run-btn">Run Agent</button>
            </form>
        </div>

        <div class="stats">
            <div class="stat-card total">
                <div class="stat-header">
                    <span class="stat-label">Total Trajectories</span>
                    <span class="stat-icon">📊</span>
                </div>
                <div class="stat-value" style="color: #4f46e5">{total}</div>
            </div>
            <div class="stat-card completed">
                <div class="stat-header">
                    <span class="stat-label">Completed</span>
                    <span class="stat-icon">✅</span>
                </div>
                <div class="stat-value" style="color: #16a34a">{completed}</div>
            </div>
            <div class="stat-card blocked">
                <div class="stat-header">
                    <span class="stat-label">Blocked</span>
                    <span class="stat-icon">🛡️</span>
                </div>
                <div class="stat-value" style="color: #dc2626">{blocked}</div>
            </div>
            <div class="stat-card drift">
                <div class="stat-header">
                    <span class="stat-label">Drift Detected</span>
                    <span class="stat-icon">⚠️</span>
                </div>
                <div class="stat-value" style="color: #d97706">{drifted}</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-header-title">
                    <h2>Recent Trajectories</h2>
                    <span class="count-pill">Last {total}</span>
                </div>
                <button class="refresh-btn" onclick="location.reload()">⟳ Refresh</button>
            </div>
            <div class="table-responsive">
                {table_or_empty}
            </div>
        </div>

        <footer>Verifiable Observability &mdash; Real-time behavioral verification for LLM agents</footer>
    </div>
</body>
</html>"""

EMPTY_STATE = """
<div class="empty-state">
    <div class="icon">📭</div>
    <p>No trajectories yet. Run <code>vo run "..."</code> or <code>vo eval sweep</code> to generate some.</p>
</div>
"""

OUTCOME_BADGE = {
    "completed": "badge-completed",
    "blocked": "badge-blocked",
    "truncated": "badge-truncated",
    "failed": "badge-failed",
}

TREND_CLASS = {
    "improving": "trend-improving",
    "degrading": "trend-degrading",
    "stable": "trend-stable",
    "insufficient_data": "trend-insufficient",
}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    engine = create_db_engine(_DB_PATH)
    traj_store = TrajectoryStore(engine)
    metrics_engine = BasicMetricsEngine()

    summaries = traj_store.list_trajectories(limit=100)

    trajectories = []
    for s in summaries:
        t = traj_store.load(s["trajectory_id"])
        if t is not None:
            trajectories.append(t)

    if not trajectories:
        return HTML_TEMPLATE.format(
            total=0, completed=0, blocked=0, drifted=0,
            table_or_empty=EMPTY_STATE,
        )

    rows = metrics_engine.compare_trajectories(trajectories)

    # Summary stats
    outcomes = [r["outcome"] for r in rows]
    completed = outcomes.count("completed")
    blocked = outcomes.count("blocked")
    drifted = sum(1 for r in rows if r["drift"] != "OK")

    # Build table HTML
    thead = """
    <table>
        <thead>
            <tr>
                <th>Traj ID</th>
                <th>Backend / Model</th>
                <th>Regime</th>
                <th>Turns</th>
                <th>Outcome</th>
                <th>Avg RCR</th>
                <th>Avg CCR</th>
                <th>RCR Trend</th>
                <th>CCR Trend</th>
                <th>Drift</th>
                <th>Failure Reason</th>
            </tr>
        </thead>
        <tbody>
    """

    tbody = ""
    for r in rows:
        outcome_cls = OUTCOME_BADGE.get(r["outcome"], "badge-default")
        drift_cls = "drift-warn" if r["drift"] != "OK" else "drift-ok"
        rcr_trend_cls = TREND_CLASS.get(r["rcr_trend"], "trend-stable")
        ccr_trend_cls = TREND_CLASS.get(r["ccr_trend"], "trend-stable")

        # avg_rcr / avg_ccr come out as pre-formatted strings ("0.750" or "—")
        avg_rcr_str = r["avg_rcr"]  # already a string from compare_trajectories
        avg_ccr_str = r["avg_ccr"]

        tbody += f"""
        <tr>
            <td><span class="mono">{r['trajectory_id']}</span></td>
            <td>{r['backend']} / <span class="mono">{r['model']}</span></td>
            <td>{r['regime']}</td>
            <td>{r['turns']}</td>
            <td><span class="{outcome_cls}">{r['outcome'].upper()}</span></td>
            <td class="metric">{avg_rcr_str}</td>
            <td class="metric">{avg_ccr_str}</td>
            <td class="{rcr_trend_cls}">{r['rcr_trend']}</td>
            <td class="{ccr_trend_cls}">{r['ccr_trend']}</td>
            <td class="{drift_cls}">{r['drift']}</td>
            <td><span style="color:#dc2626; font-size:0.78rem; font-weight:500;">{r.get('failure_reason') or ''}</span></td>
        </tr>
        """

    table_html = thead + tbody + "</tbody></table>"

    return HTML_TEMPLATE.format(
        total=len(rows),
        completed=completed,
        blocked=blocked,
        drifted=drifted,
        table_or_empty=table_html,
    )

@app.post("/run_task", response_class=RedirectResponse)
async def run_task(prompt: str = Form(...), domain: str = Form(...)):
    from verifiable_observability.core.orchestrator import Orchestrator
    from verifiable_observability.agent.factory import build_adapter
    from verifiable_observability.core.constraint_monitor import build_ccm, StubCCM
    from verifiable_observability.core.rule_bank import RuleBank, StubRuleBank
    from verifiable_observability.core.strategy_profiler import StrategyProfiler
    from verifiable_observability.storage.db import RuleStore
    from verifiable_observability.storage.models import Task, Domain

    engine = create_db_engine(_DB_PATH)
    traj_store = TrajectoryStore(engine)
    rule_store = RuleStore(engine)
    rule_bank = RuleBank(rule_store)

    try:
        task_domain = Domain(domain.lower())
    except ValueError:
        task_domain = Domain.UNKNOWN
    
    try:
        ccm = build_ccm(task_domain.value)
    except KeyError:
        ccm = StubCCM()

    # Use ollama backend now that the server is running locally
    info = build_adapter("ollama")
    
    orchestrator = Orchestrator(
        strategy_profiler=StrategyProfiler(),
        rule_bank=rule_bank,
        ccm=ccm,
        agent_adapter=info.adapter,
        trajectory_store=traj_store,
        metrics_engine=BasicMetricsEngine(),
        max_turns=5,
        agent_backend=info.backend,
        model_name=info.model_name,
    )
    
    task = Task(
        domain=task_domain,
        description=prompt,
    )
    
    # Run the orchestrator in the main thread (blocks for a bit, but fast with scripted backend)
    orchestrator.run(task)
    
    # Redirect back to index
    return RedirectResponse(url="/", status_code=303)


def run_dashboard(
    db_path: str = "verifiable_observability.db",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start the Uvicorn server for the dashboard."""
    global _DB_PATH
    _DB_PATH = os.path.abspath(db_path)

    uvicorn.run(app, host=host, port=port, log_level="info")

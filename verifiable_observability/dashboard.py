"""
Phase 8 — Minimal Dashboard.

Serves a live HTML table of recent trajectories pulled directly from the SQLite DB.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: #0f1117;
            color: #e2e8f0;
            min-height: 100vh;
            padding: 24px;
        }}

        header {{
            max-width: 1300px;
            margin: 0 auto 24px;
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        header h1 {{
            font-size: 1.6rem;
            font-weight: 700;
            background: linear-gradient(90deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .badge {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 999px;
            padding: 3px 12px;
            font-size: 0.75rem;
            color: #94a3b8;
        }}

        .stats {{
            max-width: 1300px;
            margin: 0 auto 24px;
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .stat-card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 16px 24px;
            flex: 1;
            min-width: 160px;
        }}
        .stat-label {{ font-size: 0.75rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }}
        .stat-value {{ font-size: 2rem; font-weight: 700; margin-top: 4px; }}

        .card {{
            max-width: 1300px;
            margin: 0 auto;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            overflow: hidden;
        }}
        .card-header {{
            padding: 16px 24px;
            border-bottom: 1px solid #334155;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .card-header h2 {{ font-size: 1rem; font-weight: 600; color: #cbd5e1; }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        thead th {{
            background: #0f172a;
            padding: 12px 16px;
            text-align: left;
            font-size: 0.72rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #64748b;
            white-space: nowrap;
        }}
        tbody tr {{
            border-bottom: 1px solid #1e293b;
            transition: background 0.15s;
        }}
        tbody tr:last-child {{ border-bottom: none; }}
        tbody tr:hover {{ background: #263348; }}
        td {{
            padding: 11px 16px;
            font-size: 0.86rem;
            color: #cbd5e1;
        }}
        .mono {{ font-family: 'Consolas', 'Monaco', monospace; font-size: 0.78rem; color: #94a3b8; }}

        /* Outcome badges */
        .badge-completed {{ background:#052e16; color:#4ade80; border:1px solid #166534; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }}
        .badge-blocked   {{ background:#450a0a; color:#f87171; border:1px solid #991b1b; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }}
        .badge-truncated {{ background:#1c1917; color:#fb923c; border:1px solid #92400e; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }}
        .badge-failed    {{ background:#450a0a; color:#f87171; border:1px solid #991b1b; border-radius:6px; padding:2px 8px; font-size:0.75rem; font-weight:600; }}
        .badge-default   {{ background:#1e293b; color:#94a3b8; border:1px solid #334155; border-radius:6px; padding:2px 8px; font-size:0.75rem; }}

        /* Drift badge */
        .drift-ok   {{ color:#4ade80; font-weight:600; }}
        .drift-warn {{ color:#f87171; font-weight:700; }}

        /* Trend */
        .trend-improving {{ color:#4ade80; }}
        .trend-degrading {{ color:#f87171; }}
        .trend-stable    {{ color:#94a3b8; }}
        .trend-insufficient {{ color:#475569; }}

        .metric {{ font-weight: 600; }}

        .empty-state {{
            padding: 60px;
            text-align: center;
            color: #475569;
        }}
        .empty-state .icon {{ font-size: 3rem; margin-bottom: 12px; }}
        .empty-state p {{ font-size: 0.9rem; }}

        footer {{
            max-width: 1300px;
            margin: 24px auto 0;
            font-size: 0.75rem;
            color: #475569;
            text-align: center;
        }}

        .refresh-btn {{
            background: #1d4ed8;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .refresh-btn:hover {{ background: #2563eb; }}
    </style>
</head>
<body>
    <header>
        <h1>⚡ Verifiable Observability</h1>
        <span class="badge">Phase 8 Dashboard</span>
    </header>

    <div class="stats">
        <div class="stat-card">
            <div class="stat-label">Total Trajectories</div>
            <div class="stat-value" style="color:#60a5fa">{total}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Completed</div>
            <div class="stat-value" style="color:#4ade80">{completed}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Blocked</div>
            <div class="stat-value" style="color:#f87171">{blocked}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Drift Detected</div>
            <div class="stat-value" style="color:#fb923c">{drifted}</div>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            <h2>Recent Trajectories (last {total})</h2>
            <button class="refresh-btn" onclick="location.reload()">⟳ Refresh</button>
        </div>
        {table_or_empty}
    </div>

    <footer>Verifiable Observability &mdash; Real-time behavioral verification for LLM agents</footer>
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
            <td class="mono">{r['trajectory_id']}</td>
            <td>{r['backend']} / <span class="mono">{r['model']}</span></td>
            <td>{r['regime']}</td>
            <td>{r['turns']}</td>
            <td><span class="{outcome_cls}">{r['outcome'].upper()}</span></td>
            <td class="metric">{avg_rcr_str}</td>
            <td class="metric">{avg_ccr_str}</td>
            <td class="{rcr_trend_cls}">{r['rcr_trend']}</td>
            <td class="{ccr_trend_cls}">{r['ccr_trend']}</td>
            <td class="{drift_cls}">{r['drift']}</td>
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


def run_dashboard(
    db_path: str = "verifiable_observability.db",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start the Uvicorn server for the dashboard."""
    global _DB_PATH
    _DB_PATH = os.path.abspath(db_path)

    uvicorn.run(app, host=host, port=port, log_level="info")

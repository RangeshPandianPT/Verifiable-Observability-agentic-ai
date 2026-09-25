"""
Phase 8/9 — Dashboard Redesign.
Serves a live HTML table of recent trajectories pulled directly from the SQLite DB.
"""
from __future__ import annotations

import os
import json
from typing import Any

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
    <title>Verifiable Observability</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #F8FAFC;
            --bg-surface: #FFFFFF;
            --bg-elevated: #F1F5F9;
            --border: #E2E8F0;
            --text-primary: #0F172A;
            --text-secondary: #64748B;
            --accent: #8B5CF6;
            --accent-hover: #7C3AED;
            --success: #10B981;
            --success-bg: rgba(16, 185, 129, 0.1);
            --warning: #F59E0B;
            --warning-bg: rgba(245, 158, 11, 0.1);
            --danger: #EF4444;
            --danger-bg: rgba(239, 68, 68, 0.1);
            --info: #3B82F6;
        }

        [data-theme="dark"] {
            --bg-base: #080D19;
            --bg-surface: #0D1424;
            --bg-elevated: #121B2D;
            --border: #202B40;
            --text-primary: #E8EDF7;
            --text-secondary: #8491A7;
            --accent: #8B5CF6;
            --accent-hover: #7C3AED;
            --success: #34D399;
            --success-bg: rgba(52, 211, 153, 0.1);
            --warning: #FBBF24;
            --warning-bg: rgba(251, 191, 36, 0.1);
            --danger: #FB7185;
            --danger-bg: rgba(251, 113, 133, 0.1);
            --info: #60A5FA;
        }

        .icon-btn {
            background: transparent; border: none; color: var(--text-secondary); cursor: pointer; padding: 6px; display: flex; align-items: center; justify-content: center; border-radius: 6px; transition: color 0.15s, background 0.15s;
        }
        .icon-btn:hover { color: var(--text-primary); background: var(--bg-elevated); }


        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-primary);
            display: flex;
            height: 100vh;
            overflow: hidden;
        }

        /* Sidebar */
        .sidebar {
            width: 240px;
            background-color: var(--bg-surface);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
            z-index: 20;
        }
        @media (max-width: 1024px) {
            .sidebar { display: none; }
        }
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .sidebar-logo {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 10px;
            letter-spacing: 0.02em;
        }
        .sidebar-nav {
            padding: 20px 12px;
            flex-grow: 1;
        }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 14px;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            border-radius: 6px;
            margin-bottom: 4px;
            transition: all 0.15s ease;
            cursor: pointer;
        }
        .nav-item:hover {
            color: var(--text-primary);
            background-color: var(--bg-elevated);
        }
        .nav-item.active {
            color: var(--text-primary);
            background-color: rgba(139, 92, 246, 0.1);
            border-left: 3px solid var(--accent);
            padding-left: 11px;
        }

        /* Main Content */
        .main-content {
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            position: relative;
        }
        .header {
            min-height: 64px;
            border-bottom: 1px solid var(--border);
            background-color: var(--bg-base);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 32px;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        .header-title {
            font-size: 15px;
            font-weight: 600;
            letter-spacing: 0.02em;
        }
        .header-subtitle {
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 3px;
        }
        .header-right {
            display: flex;
            align-items: center;
            gap: 20px;
            font-size: 12px;
            color: var(--text-secondary);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            background-color: var(--success);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--success);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(1.1); }
            100% { opacity: 1; transform: scale(1); }
        }
        .phase-badge {
            background-color: var(--bg-elevated);
            border: 1px solid var(--border);
            padding: 4px 8px;
            border-radius: 4px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: var(--accent);
        }

        .dashboard-container {
            padding: 32px;
            display: flex;
            flex-direction: column;
            gap: 24px;
            max-width: 1400px;
            margin: 0 auto;
            width: 100%;
        }

        /* Cards */
        .card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
        }
        .card-header {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            letter-spacing: 0.05em;
        }
        .card-header span.subtitle {
            font-size: 12px;
            color: var(--text-secondary);
            font-weight: 400;
            text-transform: none;
            letter-spacing: normal;
        }

        /* Grids */
        .grid-2 { display: grid; grid-template-columns: 2fr 1fr; gap: 24px; }
        .grid-4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 24px; }
        @media (max-width: 768px) {
            .grid-2 { grid-template-columns: 1fr; }
        }

        /* Console */
        .console-input {
            width: 100%;
            background-color: var(--bg-base);
            border: 1px solid var(--border);
            color: var(--text-primary);
            padding: 14px 16px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            margin-bottom: 16px;
            transition: border-color 0.15s;
        }
        .console-input:focus {
            outline: none;
            border-color: var(--accent);
        }
        .console-controls {
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }
        .console-select {
            background-color: var(--bg-base);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 10px 14px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
        }
        .console-select:focus { outline: none; border-color: var(--accent); }
        .btn-primary {
            background-color: var(--accent);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: background-color 0.15s;
        }
        .btn-primary:hover { background-color: var(--accent-hover); }

        /* KPI Cards */
        .kpi-card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            display: flex;
            flex-direction: column;
        }
        .kpi-label {
            font-size: 11px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
            margin-bottom: 8px;
        }
        .kpi-value {
            font-size: 28px;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin-bottom: 4px;
        }
        .progress-bg {
            height: 4px;
            background-color: var(--bg-elevated);
            border-radius: 2px;
            margin-top: auto;
            margin-bottom: 12px;
            overflow: hidden;
            width: 100%;
        }
        .progress-fill {
            height: 100%;
            border-radius: 2px;
            transition: width 0.5s ease;
        }
        .kpi-meta {
            font-size: 12px;
            color: var(--text-secondary);
        }

        /* Analytics */
        .donut-wrapper {
            position: relative;
            width: 120px;
            height: 120px;
            border-radius: 50%;
            margin: 0 auto;
        }
        .donut-hole {
            position: absolute;
            top: 20px;
            left: 20px;
            width: 80px;
            height: 80px;
            background-color: var(--bg-surface);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
        }

        /* Table */
        .table-card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            display: flex;
            flex-direction: column;
        }
        .table-header-area {
            padding: 20px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }
        .table-title {
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .action-bar {
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }
        .search-input {
            background-color: var(--bg-base);
            border: 1px solid var(--border);
            color: var(--text-primary);
            padding: 8px 12px 8px 32px;
            border-radius: 6px;
            font-size: 13px;
            width: 240px;
            background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="%238491A7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>');
            background-repeat: no-repeat;
            background-position: 10px center;
        }
        .search-input:focus { outline: none; border-color: var(--accent); }
        .btn-outline {
            background: transparent;
            color: var(--text-primary);
            border: 1px solid var(--border);
            padding: 8px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: background 0.15s;
        }
        .btn-outline:hover { background: var(--bg-elevated); }

        .table-container {
            width: 100%;
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }
        th {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            font-weight: 600;
            padding: 14px 24px;
            border-bottom: 1px solid var(--border);
            background-color: rgba(13, 20, 36, 0.4);
            white-space: nowrap;
        }
        td {
            padding: 14px 24px;
            font-size: 13px;
            border-bottom: 1px solid var(--border);
            color: var(--text-primary);
            white-space: nowrap;
            vertical-align: middle;
        }
        tr:hover { background-color: var(--bg-elevated); cursor: pointer; }
        .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }

        /* Badges */
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.03em;
        }
        .status-completed { background-color: var(--success-bg); color: var(--success); border: 1px solid rgba(52,211,153,0.2); }
        .status-blocked { background-color: var(--danger-bg); color: var(--danger); border: 1px solid rgba(251,113,133,0.2); }
        .status-drift { background-color: var(--warning-bg); color: var(--warning); border: 1px solid rgba(251,191,36,0.2); }
        .status-info { background-color: rgba(96, 165, 250, 0.1); color: var(--info); border: 1px solid rgba(96,165,250,0.2); }

        .truncate-cell {
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: var(--text-secondary);
        }

        /* Drawer */
        .drawer-overlay {
            position: fixed; inset: 0; background-color: rgba(0, 0, 0, 0.6);
            z-index: 40; opacity: 0; pointer-events: none; transition: opacity 0.2s ease;
        }
        .drawer-overlay.active { opacity: 1; pointer-events: auto; }
        .drawer {
            position: fixed; top: 0; right: 0; height: 100vh; width: 480px; max-width: 100vw;
            background-color: var(--bg-surface); border-left: 1px solid var(--border);
            z-index: 50; transform: translateX(100%); transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            display: flex; flex-direction: column; box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
        }
        .drawer.active { transform: translateX(0); }
        .drawer-header {
            padding: 24px; border-bottom: 1px solid var(--border);
            display: flex; justify-content: space-between; align-items: center;
        }
        .drawer-title { font-size: 14px; font-weight: 600; letter-spacing: 0.05em; color: var(--text-primary); }
        .drawer-close {
            background: none; border: none; color: var(--text-secondary); cursor: pointer; padding: 4px;
        }
        .drawer-close:hover { color: var(--text-primary); }
        .drawer-content { padding: 24px; overflow-y: auto; flex-grow: 1; }

        /* Drawer details */
        .detail-group { margin-bottom: 32px; }
        .detail-label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; font-weight: 600; }
        .detail-value { font-size: 14px; color: var(--text-primary); }
        .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
        
        .error-block {
            background-color: var(--danger-bg);
            border-left: 3px solid var(--danger);
            padding: 16px;
            border-radius: 0 4px 4px 0;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: var(--danger);
            white-space: pre-wrap;
            margin-bottom: 12px;
            line-height: 1.5;
        }

        /* Timeline */
        .timeline { position: relative; padding-left: 20px; margin-top: 16px; }
        .timeline::before {
            content: ''; position: absolute; top: 0; bottom: 0; left: 7px;
            width: 2px; background-color: var(--border);
        }
        .timeline-item { position: relative; margin-bottom: 24px; }
        .timeline-item:last-child { margin-bottom: 0; }
        .timeline-dot {
            position: absolute; left: -20px; top: 4px; width: 16px; height: 16px;
            border-radius: 50%; background-color: var(--bg-surface); border: 2px solid var(--border);
            display: flex; align-items: center; justify-content: center;
        }
        .timeline-dot.success { border-color: var(--success); background-color: var(--success-bg); color: var(--success); }
        .timeline-dot.danger { border-color: var(--danger); background-color: var(--danger-bg); color: var(--danger); }
        .timeline-dot.warning { border-color: var(--warning); background-color: var(--warning-bg); color: var(--warning); }
        .timeline-dot.info { border-color: var(--info); background-color: rgba(96,165,250,0.1); color: var(--info); }
        
        .timeline-content {
            background-color: var(--bg-elevated); border: 1px solid var(--border);
            padding: 14px; border-radius: 8px;
        }
        .timeline-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; color: var(--text-primary); }
        .timeline-desc { font-size: 12px; color: var(--text-secondary); line-height: 1.5; font-family: 'JetBrains Mono', monospace; white-space: pre-wrap; }

        /* Cinematic Processing Overlay */
        .proc-overlay {
            display: none; position: fixed; inset: 0; background: rgba(5, 10, 20, 0.78);
            backdrop-filter: blur(10px); z-index: 9999; justify-content: center; align-items: center;
            color: #E8EDF7; font-family: 'Inter', sans-serif;
            opacity: 0; transition: opacity 0.4s ease;
        }
        .proc-overlay.active { display: flex; }
        .proc-bg-glow {
            position: absolute; width: 500px; height: 500px;
            background: radial-gradient(circle, rgba(139,92,246,0.15) 0%, rgba(5,10,20,0) 60%);
            border-radius: 50%; pointer-events: none;
            animation: pulseGlow 4s ease-in-out infinite alternate;
        }
        @keyframes pulseGlow { 0% { transform: scale(0.8); opacity: 0.5; } 100% { transform: scale(1.1); opacity: 1; } }
        
        .proc-content {
            position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; width: 100%; max-width: 500px;
        }

        .proc-core-container {
            position: relative; width: 140px; height: 140px; display: flex; justify-content: center; align-items: center; margin-bottom: 32px;
        }
        .proc-ring {
            position: absolute; inset: 0; border-radius: 50%; border: 1px solid transparent; transition: animation-duration 1s ease;
        }
        .proc-ring-1 {
            border-top-color: rgba(139, 92, 246, 0.6); border-bottom-color: rgba(96, 165, 250, 0.4);
            animation: spinRing 4s linear infinite;
        }
        .proc-ring-2 {
            inset: 12px; border-left-color: rgba(139, 92, 246, 0.4); border-right-color: rgba(96, 165, 250, 0.6);
            animation: spinRingRev 6s linear infinite;
        }
        .proc-ring-3 {
            inset: 24px; border: 1px dashed rgba(139, 92, 246, 0.3);
            animation: spinRing 12s linear infinite;
        }
        @keyframes spinRing { 100% { transform: rotate(360deg); } }
        @keyframes spinRingRev { 100% { transform: rotate(-360deg); } }
        
        .proc-center {
            position: relative; width: 64px; height: 64px; background: rgba(13, 20, 36, 0.8);
            border: 1px solid rgba(139, 92, 246, 0.5); border-radius: 50%;
            display: flex; justify-content: center; align-items: center;
            box-shadow: 0 0 20px rgba(139, 92, 246, 0.3);
            transition: all 0.5s ease;
        }
        .proc-icon { color: #8B5CF6; transition: color 0.5s ease; }

        .proc-particles { position: absolute; inset: -40px; pointer-events: none; }
        .proc-particle {
            position: absolute; width: 3px; height: 3px; background: #60A5FA; border-radius: 50%;
            box-shadow: 0 0 6px #60A5FA; opacity: 0;
        }
        @keyframes particleFade {
            0% { opacity: 0; transform: scale(0.5); }
            50% { opacity: 0.6; transform: scale(1.2); }
            100% { opacity: 0; transform: scale(0.5); }
        }

        .proc-status-container { height: 24px; margin-bottom: 24px; overflow: hidden; position: relative; width: 100%; text-align: center; }
        .proc-status-text {
            font-size: 15px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
            color: #E8EDF7; position: absolute; width: 100%; transition: opacity 0.3s ease, color 0.5s ease;
        }

        .proc-metadata {
            display: flex; gap: 32px; margin-bottom: 32px; font-family: 'JetBrains Mono', monospace;
            font-size: 11px; padding: 12px 24px; background: rgba(13, 20, 36, 0.6);
            border: 1px solid rgba(32, 43, 64, 0.8); border-radius: 6px;
        }
        .proc-meta-row { display: flex; flex-direction: column; gap: 4px; align-items: center; }
        .proc-meta-label { color: #8491A7; font-size: 9px; letter-spacing: 0.1em; }
        .proc-meta-val { color: #E8EDF7; }

        .proc-signal {
            display: flex; gap: 4px; height: 24px; align-items: flex-end; margin-bottom: 32px;
        }
        .proc-bar {
            width: 4px; background: rgba(139, 92, 246, 0.5); border-radius: 2px;
            animation: signalPulse 1s ease-in-out infinite alternate;
            height: var(--h);
        }
        .proc-bar:nth-child(even) { animation-delay: 0.2s; background: rgba(96, 165, 250, 0.5); }
        .proc-bar:nth-child(3n) { animation-delay: 0.4s; }
        @keyframes signalPulse { 0% { transform: scaleY(0.4); } 100% { transform: scaleY(1); } }

        .proc-trajectory {
            display: flex; align-items: center; justify-content: center; width: 100%; margin-bottom: 32px;
        }
        .proc-node {
            display: flex; flex-direction: column; align-items: center; gap: 8px; width: 60px;
        }
        .proc-node-icon {
            font-size: 12px; font-weight: 800; color: #8491A7; width: 24px; height: 24px; display: flex; justify-content: center; align-items: center;
            border-radius: 50%; transition: all 0.3s ease;
        }
        .proc-node-lbl { font-size: 9px; font-weight: 600; color: #8491A7; letter-spacing: 0.05em; transition: color 0.3s ease; }
        .proc-line { flex-grow: 1; height: 1px; background: #202B40; margin: 0 -10px; max-width: 30px; margin-top: -12px; }
        
        .proc-node.active .proc-node-icon {
            color: #E8EDF7; background: rgba(139, 92, 246, 0.2); box-shadow: 0 0 10px rgba(139, 92, 246, 0.4); border: 1px solid rgba(139, 92, 246, 0.6);
        }
        .proc-node.active .proc-node-lbl { color: #8B5CF6; }
        .proc-node.done .proc-node-icon { color: #34D399; }
        .proc-node.done .proc-node-lbl { color: #E8EDF7; }

        .proc-progress { width: 100%; max-width: 300px; }
        .proc-progress-track { height: 2px; background: rgba(32, 43, 64, 0.5); position: relative; overflow: hidden; border-radius: 2px; }
        .proc-progress-thumb {
            position: absolute; top: 0; bottom: 0; width: 30%; background: linear-gradient(90deg, transparent, #8B5CF6, transparent);
            animation: progressShimmer 2s ease-in-out infinite;
        }
        @keyframes progressShimmer { 0% { left: -30%; } 100% { left: 100%; } }

        .proc-error-reason {
            display: none; margin-top: 24px; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #FB7185;
            background: rgba(251, 113, 133, 0.1); border: 1px solid rgba(251, 113, 133, 0.2); padding: 12px; border-radius: 6px; text-align: center; max-width: 400px;
        }

        @media (prefers-reduced-motion: reduce) {
            .proc-bg-glow, .proc-ring, .proc-bar, .proc-progress-thumb, .proc-particle { animation: none !important; display: none !important; }
        }
        
        .empty-state { text-align: center; padding: 40px; color: var(--text-secondary); }
    </style>
</head>
<body>
    <div id="processingOverlay" class="proc-overlay">
        <div class="proc-bg-glow"></div>
        <div class="proc-content">
            <div class="proc-core-container">
                <div class="proc-ring proc-ring-1"></div>
                <div class="proc-ring proc-ring-2"></div>
                <div class="proc-ring proc-ring-3"></div>
                <div class="proc-particles" id="proc-particles"></div>
                <div class="proc-center" id="proc-center">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="proc-icon" id="proc-icon-main"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                </div>
            </div>
            
            <div class="proc-status-container">
                <div class="proc-status-text" id="proc-status-text">Initializing agent runtime...</div>
            </div>
            
            <div class="proc-signal">
                <div class="proc-bar" style="--h: 40%"></div>
                <div class="proc-bar" style="--h: 80%"></div>
                <div class="proc-bar" style="--h: 50%"></div>
                <div class="proc-bar" style="--h: 100%"></div>
                <div class="proc-bar" style="--h: 60%"></div>
                <div class="proc-bar" style="--h: 90%"></div>
                <div class="proc-bar" style="--h: 30%"></div>
                <div class="proc-bar" style="--h: 70%"></div>
                <div class="proc-bar" style="--h: 50%"></div>
                <div class="proc-bar" style="--h: 80%"></div>
                <div class="proc-bar" style="--h: 40%"></div>
            </div>

            <div class="proc-trajectory">
                <div class="proc-node active" id="node-prompt">
                    <div class="proc-node-icon">○</div>
                    <div class="proc-node-lbl">PROMPT</div>
                </div>
                <div class="proc-line"></div>
                <div class="proc-node" id="node-plan">
                    <div class="proc-node-icon">○</div>
                    <div class="proc-node-lbl">PLAN</div>
                </div>
                <div class="proc-line"></div>
                <div class="proc-node" id="node-execute">
                    <div class="proc-node-icon">○</div>
                    <div class="proc-node-lbl">EXECUTE</div>
                </div>
                <div class="proc-line"></div>
                <div class="proc-node" id="node-observe">
                    <div class="proc-node-icon">○</div>
                    <div class="proc-node-lbl">OBSERVE</div>
                </div>
                <div class="proc-line"></div>
                <div class="proc-node" id="node-verify">
                    <div class="proc-node-icon">○</div>
                    <div class="proc-node-lbl">VERIFY</div>
                </div>
            </div>
            
            <div class="proc-metadata">
                <div class="proc-meta-row"><span class="proc-meta-label">MODEL</span><span class="proc-meta-val">llama3.2:3b</span></div>
                <div class="proc-meta-row"><span class="proc-meta-label">BACKEND</span><span class="proc-meta-val">Ollama</span></div>
                <div class="proc-meta-row"><span class="proc-meta-label">REGIME</span><span class="proc-meta-val" id="proc-meta-regime">Finance</span></div>
            </div>

            <div class="proc-progress">
                <div class="proc-progress-track">
                    <div class="proc-progress-thumb" id="proc-progress-thumb"></div>
                </div>
            </div>
            
            <div class="proc-error-reason" id="proc-error-reason"></div>
        </div>
    </div>

    <!-- Sidebar -->
    <aside class="sidebar">
        <div class="sidebar-header">
            <div class="sidebar-logo">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--accent)"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                VERIFIABLE OBS
            </div>
        </div>
        <nav class="sidebar-nav">
            <a href="#overview" class="nav-item" onclick="switchPage('overview')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
                Overview
            </a>
            <a href="#trajectories" class="nav-item active" onclick="switchPage('trajectories')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                Trajectories
            </a>
            <a href="#agent-runs" class="nav-item" onclick="switchPage('agent-runs')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                Agent Runs
            </a>
            <a href="#drift-detection" class="nav-item" onclick="switchPage('drift-detection')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                Drift Detection
            </a>
            <a href="#policies" class="nav-item" onclick="switchPage('policies')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                Policies
            </a>
        </nav>
        <div style="padding: 20px 12px; border-top: 1px solid var(--border);">
            <a href="#settings" class="nav-item" onclick="switchPage('settings')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
                Settings
            </a>
        </div>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
        <header class="header">
            <div>
                <div class="header-title">Verifiable Observability</div>
                <div class="header-subtitle">Real-Time Agent Trajectory & Behavioral Verification Engine</div>
            </div>
            <div class="header-right">
                <button class="icon-btn" onclick="toggleTheme()" title="Toggle Dark Mode">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" id="theme-icon-moon" style="display:none;"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" id="theme-icon-sun" style="display:none;"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
                </button>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span class="status-dot"></span>
                    Operational
                </div>
                <div id="last-updated">Updated just now</div>
                <div class="phase-badge">Phase 9</div>
            </div>
        </header>

        <!-- OVERVIEW PAGE -->
        <div id="page-overview" class="dashboard-container" style="display: none;">
            <div class="card">
                <div class="card-header">SYSTEM OVERVIEW</div>
                <div class="empty-state">
                    <div style="font-size:32px; margin-bottom: 12px;">📊</div>
                    <div style="font-size:14px; color: var(--text-primary); font-weight:600; margin-bottom: 6px;">Overview Dashboard</div>
                    <p style="font-size:13px;">High-level system metrics and performance trends will appear here.</p>
                </div>
            </div>
        </div>

        <!-- TRAJECTORIES PAGE -->
        <div id="page-trajectories" class="dashboard-container">
            <!-- Top section: Console and Analytics -->
            <div class="grid-2">
                <!-- Agent Console -->
                <div class="card">
                    <div class="card-header">
                        TEST AGENT
                        <span class="subtitle">Execute a controlled trajectory</span>
                    </div>
                    <form method="POST" action="/run_task" onsubmit="handleRunTask(event)">
                        <input type="text" name="prompt" class="console-input" placeholder="Transfer $500 from ACC-001 to ACC-002" required />
                        <div class="console-controls">
                            <select name="domain" class="console-select">
                                <option value="finance">Finance Regime</option>
                                <option value="healthcare">Healthcare Regime</option>
                                <option value="code_execution">Code Exec Regime</option>
                            </select>
                            <button type="submit" class="btn-primary">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                                Run Agent
                            </button>
                        </div>
                    </form>
                </div>

                <!-- Analytics Distribution -->
                <div class="card" style="display: flex; flex-direction: column; justify-content: space-between;">
                    <div class="card-header" style="margin-bottom: 8px;">
                        OUTCOME DISTRIBUTION
                    </div>
                    <div style="display: flex; justify-content: space-around; align-items: center; flex-grow: 1;">
                        <div style="text-align: center;">
                            <div class="donut-wrapper" id="outcome-donut">
                                <div class="donut-hole">
                                    <div style="font-size:22px; font-weight:700;" id="outcome-total-lbl">0</div>
                                    <div style="font-size:11px; color:var(--text-secondary)">Total</div>
                                </div>
                            </div>
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 14px;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="width: 12px; height: 12px; background-color: var(--success); border-radius: 3px;"></span>
                                <span style="font-size: 13px; color: var(--text-secondary);">Completed: <span id="dist-completed" style="color:var(--text-primary); font-weight:500;">0%</span></span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="width: 12px; height: 12px; background-color: var(--danger); border-radius: 3px;"></span>
                                <span style="font-size: 13px; color: var(--text-secondary);">Blocked: <span id="dist-blocked" style="color:var(--text-primary); font-weight:500;">0%</span></span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="width: 12px; height: 12px; background-color: var(--warning); border-radius: 3px;"></span>
                                <span style="font-size: 13px; color: var(--text-secondary);">Other: <span id="dist-other" style="color:var(--text-primary); font-weight:500;">0%</span></span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- KPI Cards -->
            <div class="grid-4" id="kpi-cards">
                <!-- JS populated -->
            </div>

            <!-- Table -->
            <div class="table-card">
                <div class="table-header-area">
                    <div class="table-title">RECENT TRAJECTORIES</div>
                    <div class="action-bar">
                        <input type="text" class="search-input" id="search-input" placeholder="Search trajectories, models, errors..." onkeyup="filterTable()">
                        <select class="console-select" id="filter-status" onchange="filterTable()">
                            <option value="ALL">All Status</option>
                            <option value="COMPLETED">Completed</option>
                            <option value="BLOCKED">Blocked</option>
                            <option value="FLAGGED">Flagged</option>
                        </select>
                        <select class="console-select" id="filter-drift" onchange="filterTable()">
                            <option value="ALL">All Drift</option>
                            <option value="⚠ DETECTED">Drift Detected</option>
                            <option value="✓ NO DRIFT">No Drift</option>
                        </select>
                        <button class="btn-outline" onclick="location.reload()">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                            Refresh
                        </button>
                    </div>
                </div>
                
                <div class="table-container">
                    <table id="traj-table">
                        <thead>
                            <tr>
                                <th>Trajectory</th>
                                <th>Model</th>
                                <th>Regime</th>
                                <th>Turns</th>
                                <th>Outcome</th>
                                <th>RCR</th>
                                <th>CCR</th>
                                <th>Drift</th>
                                <th>Failure Reason</th>
                            </tr>
                        </thead>
                        <tbody id="traj-tbody">
                            <!-- JS populated -->
                        </tbody>
                    </table>
                    <div id="empty-state" class="empty-state" style="display:none;">
                        <div style="font-size:32px; margin-bottom: 12px;">📭</div>
                        <div style="font-size:14px; color: var(--text-primary); font-weight:600; margin-bottom: 6px;">No trajectories found</div>
                        <p style="font-size:13px;">Run an agent to generate your first verified trajectory.</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- AGENT RUNS PAGE -->
        <div id="page-agent-runs" class="dashboard-container" style="display: none;">
            <div class="card">
                <div class="card-header">AGENT RUNS</div>
                <div class="empty-state">
                    <div style="font-size:32px; margin-bottom: 12px;">🤖</div>
                    <div style="font-size:14px; color: var(--text-primary); font-weight:600; margin-bottom: 6px;">Agent Execution History</div>
                    <p style="font-size:13px;">Detailed logs and memory states for individual agent runs.</p>
                </div>
            </div>
        </div>

        <!-- DRIFT DETECTION PAGE -->
        <div id="page-drift-detection" class="dashboard-container" style="display: none;">
            <div class="card">
                <div class="card-header">DRIFT DETECTION</div>
                <div class="empty-state">
                    <div style="font-size:32px; margin-bottom: 12px;">📈</div>
                    <div style="font-size:14px; color: var(--text-primary); font-weight:600; margin-bottom: 6px;">Behavioral Drift Analysis</div>
                    <p style="font-size:13px;">Monitor agent behavior changes and performance degradation over time.</p>
                </div>
            </div>
        </div>

        <!-- POLICIES PAGE -->
        <div id="page-policies" class="dashboard-container" style="display: none;">
            <div class="card">
                <div class="card-header">SAFETY POLICIES & CONSTRAINTS</div>
                <div class="empty-state">
                    <div style="font-size:32px; margin-bottom: 12px;">🛡️</div>
                    <div style="font-size:14px; color: var(--text-primary); font-weight:600; margin-bottom: 6px;">Policy Management</div>
                    <p style="font-size:13px;">Define and manage Constraint Check Models (CCMs) and fail-safe rules.</p>
                </div>
            </div>
        </div>

        <!-- SETTINGS PAGE -->
        <div id="page-settings" class="dashboard-container" style="display: none;">
            <div class="card">
                <div class="card-header">SYSTEM SETTINGS</div>
                <div class="empty-state">
                    <div style="font-size:32px; margin-bottom: 12px;">⚙️</div>
                    <div style="font-size:14px; color: var(--text-primary); font-weight:600; margin-bottom: 6px;">Configuration</div>
                    <p style="font-size:13px;">Manage database connections, API keys, and system preferences.</p>
                </div>
            </div>
        </div>
    </main>

    <!-- Drawer Overlay -->
    <div class="drawer-overlay" id="drawerOverlay" onclick="closeDrawer()"></div>
    
    <!-- Drawer -->
    <div class="drawer" id="detailDrawer">
        <div class="drawer-header">
            <div class="drawer-title">TRAJECTORY DETAILS</div>
            <button class="drawer-close" onclick="closeDrawer()">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
            </button>
        </div>
        <div class="drawer-content" id="drawerContent">
            <!-- JS populated -->
        </div>
    </div>

    <script>
        function switchPage(pageId) {
            document.querySelectorAll('.nav-item').forEach(el => {
                if(el.getAttribute('href') === '#' + pageId) {
                    el.classList.add('active');
                } else {
                    el.classList.remove('active');
                }
            });
            
            const pages = ['overview', 'trajectories', 'agent-runs', 'drift-detection', 'policies', 'settings'];
            pages.forEach(p => {
                const el = document.getElementById('page-' + p);
                if(el) {
                    el.style.display = (p === pageId) ? 'flex' : 'none';
                }
            });
            
            window.history.pushState(null, null, '#' + pageId);
        }
        
        window.addEventListener('DOMContentLoaded', () => {
            const hash = window.location.hash.replace('#', '');
            if(hash && ['overview', 'trajectories', 'agent-runs', 'drift-detection', 'policies', 'settings'].includes(hash)) {
                switchPage(hash);
            }
        });

        function toggleTheme() {
            const html = document.documentElement;
            if (html.getAttribute('data-theme') === 'dark') {
                html.removeAttribute('data-theme');
                document.getElementById('theme-icon-moon').style.display = 'block';
                document.getElementById('theme-icon-sun').style.display = 'none';
                localStorage.setItem('theme', 'light');
            } else {
                html.setAttribute('data-theme', 'dark');
                document.getElementById('theme-icon-moon').style.display = 'none';
                document.getElementById('theme-icon-sun').style.display = 'block';
                localStorage.setItem('theme', 'dark');
            }
        }
        
        // initialize theme before anything else
        (function() {
            const savedTheme = localStorage.getItem('theme') || 'light';
            if (savedTheme === 'dark') {
                document.documentElement.setAttribute('data-theme', 'dark');
            }
        })();
        
        document.addEventListener('DOMContentLoaded', () => {
            const savedTheme = localStorage.getItem('theme') || 'light';
            if (savedTheme === 'dark') {
                document.getElementById('theme-icon-sun').style.display = 'block';
            } else {
                document.getElementById('theme-icon-moon').style.display = 'block';
            }
        });

        const rawData = {traj_data_json}; 
        let trajectories = rawData.rows || [];
        let detailsMap = rawData.details || {};

        function renderDashboard() {
            if (trajectories.length === 0) {
                document.getElementById('empty-state').style.display = 'block';
                document.getElementById('traj-tbody').innerHTML = '';
                document.getElementById('kpi-cards').innerHTML = '';
                document.getElementById('outcome-total-lbl').innerText = '0';
                return;
            }
            
            document.getElementById('empty-state').style.display = 'none';
            
            document.getElementById('last-updated').innerText = 'Updated ' + new Date().toLocaleTimeString();

            // Stats
            const total = trajectories.length;
            const completed = trajectories.filter(t => t.outcome === 'completed').length;
            const blocked = trajectories.filter(t => t.outcome === 'blocked').length;
            const drifted = trajectories.filter(t => t.drift !== 'OK').length;
            
            const pctC = Math.round((completed / total) * 100) || 0;
            const pctB = Math.round((blocked / total) * 100) || 0;
            const pctO = 100 - pctC - pctB;

            document.getElementById('outcome-total-lbl').innerText = total;
            document.getElementById('dist-completed').innerText = pctC + '%';
            document.getElementById('dist-blocked').innerText = pctB + '%';
            document.getElementById('dist-other').innerText = pctO + '%';
            
            document.getElementById('outcome-donut').style.background = `conic-gradient(var(--success) 0% ${pctC}%, var(--danger) ${pctC}% ${pctC + pctB}%, var(--border) ${pctC + pctB}% 100%)`;

            const kpiHtml = `
                <div class="kpi-card">
                    <div class="kpi-label">Total Trajectories</div>
                    <div class="kpi-value">${total}</div>
                    <div class="progress-bg"><div class="progress-fill" style="width: 100%; background: var(--info);"></div></div>
                    <div class="kpi-meta">100% tracked</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Completed</div>
                    <div class="kpi-value">${completed}</div>
                    <div class="progress-bg"><div class="progress-fill" style="width: ${pctC}%; background: var(--success);"></div></div>
                    <div class="kpi-meta">${pctC}% of trajectories</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Blocked</div>
                    <div class="kpi-value">${blocked}</div>
                    <div class="progress-bg"><div class="progress-fill" style="width: ${pctB}%; background: var(--danger);"></div></div>
                    <div class="kpi-meta">${pctB}% of trajectories</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Drift Detected</div>
                    <div class="kpi-value">${drifted}</div>
                    <div class="progress-bg"><div class="progress-fill" style="width: ${Math.round((drifted/total)*100)}%; background: var(--warning);"></div></div>
                    <div class="kpi-meta">${Math.round((drifted/total)*100)}% of trajectories</div>
                </div>
            `;
            document.getElementById('kpi-cards').innerHTML = kpiHtml;

            renderTable(trajectories);
        }

        function getStatusBadge(outcome) {
            outcome = (outcome || '').toLowerCase();
            if (outcome === 'completed') return `<span class="status-badge status-completed">✓ COMPLETED</span>`;
            if (outcome === 'blocked') return `<span class="status-badge status-blocked">✕ BLOCKED</span>`;
            if (outcome === 'flagged') return `<span class="status-badge status-drift">⚠ FLAGGED</span>`;
            if (outcome === 'failed') return `<span class="status-badge status-blocked">✕ FAILED</span>`;
            return `<span class="status-badge status-info">○ ${outcome.toUpperCase()}</span>`;
        }

        function getDriftBadge(drift) {
            if (drift === 'OK') return `<span class="status-badge status-completed">✓ NO DRIFT</span>`;
            return `<span class="status-badge status-drift">⚠ DETECTED</span>`;
        }

        function renderTable(data) {
            const tbody = document.getElementById('traj-tbody');
            tbody.innerHTML = '';
            
            if (data.length === 0) {
                document.getElementById('empty-state').style.display = 'block';
            } else {
                document.getElementById('empty-state').style.display = 'none';
            }

            data.forEach(t => {
                const tr = document.createElement('tr');
                tr.onclick = () => openDrawer(t.full_id);
                
                let failReason = t.failure_reason || '-';
                
                tr.innerHTML = `
                    <td><span class="mono">${t.trajectory_id}</span></td>
                    <td><span class="mono">${t.model}</span></td>
                    <td>${t.regime}</td>
                    <td>${t.turns}</td>
                    <td>${getStatusBadge(t.outcome)}</td>
                    <td class="mono">${t.avg_rcr}</td>
                    <td class="mono">${t.avg_ccr}</td>
                    <td>${getDriftBadge(t.drift)}</td>
                    <td class="truncate-cell" title="${failReason.replace(/"/g, '&quot;')}">${failReason}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        function filterTable() {
            const search = document.getElementById('search-input').value.toLowerCase();
            const statusFilter = document.getElementById('filter-status').value;
            const driftFilter = document.getElementById('filter-drift').value;
            
            const filtered = trajectories.filter(t => {
                const searchMatch = !search || 
                    t.trajectory_id.toLowerCase().includes(search) || 
                    t.model.toLowerCase().includes(search) || 
                    (t.failure_reason && t.failure_reason.toLowerCase().includes(search));
                    
                const sMatch = statusFilter === 'ALL' || t.outcome.toUpperCase() === statusFilter;
                
                const dMatch = driftFilter === 'ALL' || 
                               (driftFilter === '⚠ DETECTED' && t.drift !== 'OK') || 
                               (driftFilter === '✓ NO DRIFT' && t.drift === 'OK');
                               
                return searchMatch && sMatch && dMatch;
            });
            renderTable(filtered);
        }

        function openDrawer(id) {
            const rowData = trajectories.find(t => t.full_id === id);
            const detailData = detailsMap[id];
            
            if (!rowData) return;

            let html = `
                <div class="detail-group">
                    <div style="margin-bottom:20px;">${getStatusBadge(rowData.outcome)}</div>
                    
                    <div class="detail-grid">
                        <div>
                            <div class="detail-label">Trajectory ID</div>
                            <div class="detail-value mono" style="font-size:12px;">${id}</div>
                        </div>
                        <div>
                            <div class="detail-label">Backend</div>
                            <div class="detail-value">${rowData.backend}</div>
                        </div>
                        <div>
                            <div class="detail-label">Model</div>
                            <div class="detail-value mono">${rowData.model}</div>
                        </div>
                        <div>
                            <div class="detail-label">Regime</div>
                            <div class="detail-value">${rowData.regime}</div>
                        </div>
                        <div>
                            <div class="detail-label">Turns</div>
                            <div class="detail-value">${rowData.turns}</div>
                        </div>
                        <div>
                            <div class="detail-label">Drift</div>
                            <div class="detail-value">${getDriftBadge(rowData.drift)}</div>
                        </div>
                    </div>
                </div>
                
                <div class="detail-group">
                    <div class="detail-grid">
                        <div>
                            <div class="detail-label">RCR (Reasoning)</div>
                            <div class="detail-value mono" style="font-size:24px; font-weight:700;">${rowData.avg_rcr}</div>
                        </div>
                        <div>
                            <div class="detail-label">CCR (Constraint)</div>
                            <div class="detail-value mono" style="font-size:24px; font-weight:700;">${rowData.avg_ccr}</div>
                        </div>
                    </div>
                </div>
            `;

            if (rowData.failure_reason) {
                html += `
                    <div class="detail-group">
                        <div class="detail-label" style="display: flex; justify-content: space-between;">
                            <span>FAILURE ANALYSIS</span>
                            <button class="btn-outline" style="padding: 2px 6px; font-size: 10px;" onclick="navigator.clipboard.writeText(document.getElementById('errorBlock').innerText)">
                                Copy Error
                            </button>
                        </div>
                        <div class="error-block" id="errorBlock">${rowData.failure_reason.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
                    </div>
                `;
            }

            if (detailData && detailData.timeline && detailData.timeline.length > 0) {
                html += `
                    <div class="detail-group" style="margin-top: 32px;">
                        <div class="detail-label">BEHAVIORAL TRACE</div>
                        <div class="timeline">
                `;
                
                detailData.timeline.forEach(item => {
                    let dotClass = 'info';
                    let icon = '';
                    if (item.status === 'success') {
                        dotClass = 'success';
                        icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                    } else if (item.status === 'blocked') {
                        dotClass = 'danger';
                        icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
                    } else if (item.status === 'warning') {
                        dotClass = 'warning';
                        icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="9" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';
                    } else {
                        icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle></svg>';
                    }

                    html += `
                        <div class="timeline-item">
                            <div class="timeline-dot ${dotClass}">${icon}</div>
                            <div class="timeline-content">
                                <div class="timeline-title">${item.event}</div>
                                <div class="timeline-desc">${item.desc.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
                            </div>
                        </div>
                    `;
                });
                
                html += `</div></div>`;
            }

            document.getElementById('drawerContent').innerHTML = html;
            document.getElementById('drawerOverlay').classList.add('active');
            document.getElementById('detailDrawer').classList.add('active');
        }

        function closeDrawer() {
            document.getElementById('drawerOverlay').classList.remove('active');
            document.getElementById('detailDrawer').classList.remove('active');
        }

        document.addEventListener('DOMContentLoaded', renderDashboard);
        // Cinematic Processing Overlay Logic
        let procStatusInterval;
        let procNodeInterval;
        const statusMessages = [
            "Initializing agent runtime...",
            "Building trajectory...",
            "Executing agent...",
            "Observing agent behavior...",
            "Evaluating tool interactions...",
            "Checking behavioral constraints...",
            "Analyzing trajectory...",
            "Running verification checks..."
        ];

        function resetProcessingState() {
            document.getElementById('proc-status-text').innerText = statusMessages[0];
            document.getElementById('proc-status-text').style.opacity = 1;
            document.getElementById('proc-status-text').style.color = '#E8EDF7';
            document.getElementById('proc-center').style.borderColor = 'rgba(139, 92, 246, 0.5)';
            document.getElementById('proc-icon-main').style.color = '#8B5CF6';
            document.getElementById('proc-icon-main').innerHTML = '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>';
            document.getElementById('proc-error-reason').style.display = 'none';
            document.querySelectorAll('.proc-ring').forEach(el => { el.style.animationPlayState = 'running'; el.style.animationDuration = el.classList.contains('proc-ring-3') ? '12s' : (el.classList.contains('proc-ring-2') ? '6s' : '4s'); });
            document.getElementById('proc-progress-thumb').style.animationPlayState = 'running';
            
            const nodes = ['prompt', 'plan', 'execute', 'observe', 'verify'];
            nodes.forEach(n => {
                const el = document.getElementById('node-' + n);
                el.className = 'proc-node';
                el.querySelector('.proc-node-icon').innerText = '○';
            });
            document.getElementById('node-prompt').classList.add('active');
        }

        function startSimulatedProgress() {
            let msgIdx = 0;
            procStatusInterval = setInterval(() => {
                msgIdx = (msgIdx + 1) % statusMessages.length;
                const el = document.getElementById('proc-status-text');
                el.style.opacity = 0;
                setTimeout(() => {
                    el.innerText = statusMessages[msgIdx];
                    el.style.opacity = 1;
                }, 300);
            }, 2500);

            const nodes = ['prompt', 'plan', 'execute', 'observe', 'verify'];
            let nodeIdx = 0;
            procNodeInterval = setInterval(() => {
                if (nodeIdx < nodes.length - 1) {
                    const current = document.getElementById('node-' + nodes[nodeIdx]);
                    current.classList.remove('active');
                    current.classList.add('done');
                    current.querySelector('.proc-node-icon').innerText = '✓';
                    nodeIdx++;
                    document.getElementById('node-' + nodes[nodeIdx]).classList.add('active');
                }
            }, 3000);
            
            // Generate particles
            if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                const particlesContainer = document.getElementById('proc-particles');
                particlesContainer.innerHTML = '';
                for(let i = 0; i < 14; i++) {
                    const p = document.createElement('div');
                    p.className = 'proc-particle';
                    const angle = Math.random() * Math.PI * 2;
                    const radius = 60 + Math.random() * 25;
                    p.style.left = `calc(50% + ${Math.cos(angle)*radius}px)`;
                    p.style.top = `calc(50% + ${Math.sin(angle)*radius}px)`;
                    p.style.animation = `particleFade ${2 + Math.random()*2}s ease-in-out infinite`;
                    p.style.animationDelay = `${Math.random()*2}s`;
                    particlesContainer.appendChild(p);
                }
            }
        }

        function finishProcessing(outcome, reason, callback) {
            clearInterval(procStatusInterval);
            clearInterval(procNodeInterval);
            
            const nodes = ['prompt', 'plan', 'execute', 'observe', 'verify'];
            nodes.forEach(n => {
                const el = document.getElementById('node-' + n);
                el.classList.remove('active');
                el.classList.add('done');
                el.querySelector('.proc-node-icon').innerText = '✓';
            });

            document.querySelectorAll('.proc-ring').forEach(el => el.style.animationDuration = '20s'); // slow down
            document.getElementById('proc-progress-thumb').style.animationPlayState = 'paused';

            const center = document.getElementById('proc-center');
            const icon = document.getElementById('proc-icon-main');
            const status = document.getElementById('proc-status-text');
            
            status.style.opacity = 0;
            
            setTimeout(() => {
                status.style.opacity = 1;
                if (outcome === 'completed') {
                    center.style.borderColor = '#34D399';
                    icon.style.color = '#34D399';
                    icon.innerHTML = '<polyline points="20 6 9 17 4 12"></polyline>';
                    status.style.color = '#34D399';
                    status.innerText = '✓ Trajectory Verified';
                } else if (outcome === 'blocked' || outcome === 'flagged') {
                    center.style.borderColor = '#FBBF24';
                    icon.style.color = '#FBBF24';
                    icon.innerHTML = '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>';
                    status.style.color = '#FBBF24';
                    status.innerText = '✕ Trajectory Blocked';
                    if (reason) {
                        const rEl = document.getElementById('proc-error-reason');
                        rEl.innerText = reason;
                        rEl.style.display = 'block';
                    }
                } else {
                    center.style.borderColor = '#FB7185';
                    icon.style.color = '#FB7185';
                    icon.innerHTML = '<circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line>';
                    status.style.color = '#FB7185';
                    status.innerText = '! Execution Failed';
                    if (reason) {
                        const rEl = document.getElementById('proc-error-reason');
                        rEl.innerText = reason;
                        rEl.style.display = 'block';
                    }
                }
                
                setTimeout(() => {
                    const overlay = document.getElementById('processingOverlay');
                    overlay.style.opacity = 0;
                    setTimeout(() => {
                        overlay.classList.remove('active');
                        callback();
                    }, 400);
                }, 1000);
            }, 300);
        }

        async function handleRunTask(event) {
            event.preventDefault();
            const form = event.target;
            const formData = new FormData(form);
            
            const domainMap = { 'finance': 'Finance', 'healthcare': 'Healthcare', 'code_execution': 'Code Exec' };
            document.getElementById('proc-meta-regime').innerText = domainMap[formData.get('domain')] || formData.get('domain');
            
            resetProcessingState();
            const overlay = document.getElementById('processingOverlay');
            overlay.classList.add('active');
            
            // Force reflow for transitions
            void overlay.offsetWidth;
            overlay.style.opacity = 1;
            
            startSimulatedProgress();
            
            try {
                const response = await fetch(form.action, { method: form.method, body: formData, redirect: 'follow' });
                const htmlText = await response.text();
                
                const match = htmlText.match(/const rawData = ({.*?});/s);
                let outcome = 'completed';
                let reason = '';
                if (match && match[1]) {
                    const newData = JSON.parse(match[1]);
                    if (newData.rows && newData.rows.length > 0) {
                        outcome = newData.rows[0].outcome;
                        reason = newData.rows[0].failure_reason;
                        
                        // Update global state with the new data
                        window.trajectories = newData.rows;
                        window.detailsMap = newData.details;
                    }
                }
                
                finishProcessing(outcome, reason, () => {
                    if (typeof renderDashboard === 'function') {
                        renderDashboard();
                    } else {
                        location.reload();
                    }
                });
            } catch (err) {
                finishProcessing('failed', 'Network Error: ' + err.message, () => {
                    location.reload();
                });
            }
        }
    </script>
</body>
</html>
"""

def _extract_timeline(traj) -> list[dict[str, Any]]:
    timeline = []
    
    # Task Start
    desc = getattr(traj.task, "description", "") if hasattr(traj, "task") else "Task Start"
    timeline.append({
        "event": "USER PROMPT",
        "status": "info",
        "desc": desc
    })
    
    for turn in traj.turns:
        if getattr(turn, "decisions", None):
            timeline.append({
                "event": "PLAN / REASONING",
                "status": "success",
                "desc": turn.decisions[0].reasoning
            })
        
        if getattr(turn, "actions", None):
            act = turn.actions[0]
            timeline.append({
                "event": "TOOL CALL",
                "status": "success",
                "desc": f"Tool: {act.tool_name}\\nParams: {act.parameters}"
            })
            
        blocked_or_flagged = False
        if getattr(turn, "constraint_checks", None):
            for c in turn.constraint_checks:
                if c.decision.value == "BLOCK":
                    blocked_or_flagged = True
                    timeline.append({
                        "event": "CCM BLOCK",
                        "status": "blocked",
                        "desc": c.details or "Execution blocked by policy"
                    })
                elif c.decision.value == "FLAG":
                    blocked_or_flagged = True
                    timeline.append({
                        "event": "CCM FLAG",
                        "status": "warning",
                        "desc": c.details or "Execution flagged by policy"
                    })
        
        if not blocked_or_flagged and getattr(turn, "actions", None):
             timeline.append({
                "event": "VALIDATION",
                "status": "success",
                "desc": "Passed all CCM checks."
            })
             
    if traj.outcome.value == "failed":
         timeline.append({
            "event": "EXECUTION FAILED",
            "status": "blocked",
            "desc": traj.failure_reason or "Unknown error"
        })
    elif traj.outcome.value == "completed":
        timeline.append({
            "event": "COMPLETED",
            "status": "success",
            "desc": "Trajectory finished successfully."
        })
        
    return timeline


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    engine = create_db_engine(_DB_PATH)
    traj_store = TrajectoryStore(engine)
    metrics_engine = BasicMetricsEngine()

    summaries = traj_store.list_trajectories(limit=100)

    trajectories = []
    for s in summaries:
        try:
            t = traj_store.load(s["trajectory_id"])
            if t is not None:
                trajectories.append(t)
        except ValueError:
            continue

    if not trajectories:
        return HTML_TEMPLATE.replace("{traj_data_json}", "{}")

    rows = metrics_engine.compare_trajectories(trajectories)
    
    # Enrich rows with full id for the drawer
    for r, t in zip(rows, trajectories):
        r["full_id"] = t.trajectory_id

    details_map = {}
    for t in trajectories:
        details_map[t.trajectory_id] = {
            "timeline": _extract_timeline(t)
        }

    data_payload = {
        "rows": rows,
        "details": details_map
    }
    
    # Safe JSON dump
    json_str = json.dumps(data_payload)
    
    return HTML_TEMPLATE.replace("{traj_data_json}", json_str)

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

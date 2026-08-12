"""
Verifiable Observability — Real-time behavioral verification for multi-turn LLM agents.

Architecture:
  1. Strategy Profiler  — classifies task structure and sets a behavioral baseline
  2. Rule Bank          — auditable versioned store of observation→action mappings
  3. Constraint Compliance Monitor — enforces safety constraints before action dispatch

Metrics:
  - RCR (Reasoning Consistency Ratio)  — fraction of decisions traceable to verified rules
  - CCR (Constraint Compliance Ratio)  — fraction of actions satisfying all active constraints
"""

__version__ = "0.1.0"

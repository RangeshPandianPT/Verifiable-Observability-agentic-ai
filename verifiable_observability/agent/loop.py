"""
Minimal ReAct-style think→act→observe loop.

This module provides the AgentLoop wrapper that drives a single agent session
using the Orchestrator.  It is the entry point used by the CLI and simulation
harness.

In Phase 4 this will be extended with real LLM calls via the adapter.
"""

from __future__ import annotations

from verifiable_observability.core.orchestrator import Orchestrator
from verifiable_observability.storage.models import Task, Trajectory


class AgentLoop:
    """
    Thin wrapper around the Orchestrator that provides a clean run() interface.

    The Orchestrator already implements the full loop; AgentLoop exists to give
    higher-level code (CLI, harness) a stable entry point that can later be
    extended with pre/post-run hooks (e.g. warm-up, cool-down, result logging)
    without touching the Orchestrator.
    """

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    def run(self, task: Task) -> Trajectory:
        """Run a full agent trajectory for the given task."""
        return self.orchestrator.run(task)

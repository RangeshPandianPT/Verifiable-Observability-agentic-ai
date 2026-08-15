"""
Adapter Factory — resolves config.yaml / env vars to the correct AgentAdapterBase.

Usage::

    from verifiable_observability.agent.factory import build_adapter, AdapterInfo
    info = build_adapter()          # reads config.yaml
    info = build_adapter("ollama")  # explicit backend override

Returns an AdapterInfo namedtuple with:
    adapter    : AgentAdapterBase instance, ready to use
    backend    : str  — "ollama" | "anthropic" | "openai" | "scripted"
    model_name : str  — the model ID that was actually selected

The Orchestrator stores backend + model_name on every Trajectory
for cross-model RCR/CCR comparison in Phase 5-7 analysis.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Path to config.yaml relative to the project root
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


# ---------------------------------------------------------------------------
# Public return type
# ---------------------------------------------------------------------------


class AdapterInfo(NamedTuple):
    adapter: object      # AgentAdapterBase — typed loosely to avoid circular imports
    backend: str
    model_name: str


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Load config.yaml; return empty dict if file is missing."""
    try:
        import yaml  # pyyaml
    except ImportError:
        logger.warning("pyyaml not installed — using empty config defaults.")
        return {}

    if not _CONFIG_PATH.exists():
        logger.warning("config.yaml not found at %s — using defaults.", _CONFIG_PATH)
        return {}

    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_adapter(backend_override: str | None = None) -> AdapterInfo:
    """
    Build the correct AgentAdapterBase based on config.yaml + env vars.

    Priority order for backend selection:
        1. backend_override argument (explicit call-site override)
        2. AGENT_BACKEND environment variable
        3. agent_backend key in config.yaml
        4. Default: "ollama"

    Priority order for model selection:
        1. AGENT_MODEL environment variable
        2. Model key under the selected backend section in config.yaml
        3. Per-backend hardcoded default

    Returns:
        AdapterInfo(adapter, backend, model_name)

    Raises:
        ValueError  if the selected backend is unknown
        RuntimeError if a required API key / server is unavailable
    """
    cfg = _load_config()

    # Resolve backend
    backend = (
        backend_override
        or os.environ.get("AGENT_BACKEND")
        or cfg.get("agent_backend", "ollama")
    ).lower()

    logger.info("Building adapter | backend=%s", backend)

    if backend == "ollama":
        return _build_ollama(cfg)
    elif backend == "anthropic":
        return _build_anthropic(cfg)
    elif backend == "openai":
        return _build_openai(cfg)
    else:
        raise ValueError(
            f"Unknown agent_backend: {backend!r}. "
            "Valid options: 'ollama', 'anthropic', 'openai'."
        )


# ---------------------------------------------------------------------------
# Per-backend builders
# ---------------------------------------------------------------------------


def _build_ollama(cfg: dict) -> AdapterInfo:
    from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter

    ollama_cfg = cfg.get("ollama", {})

    base_url = os.environ.get("OLLAMA_BASE_URL") or ollama_cfg.get(
        "base_url", "http://localhost:11434/v1"
    )
    model = os.environ.get("AGENT_MODEL") or ollama_cfg.get("model", "llama3.2:3b")
    timeout = int(os.environ.get("OLLAMA_TIMEOUT", str(ollama_cfg.get("timeout_seconds", 120))))
    max_retries = int(ollama_cfg.get("max_retries", 2))
    json_fallback = bool(ollama_cfg.get("json_action_fallback", True))

    adapter = OllamaAgentAdapter(
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
        json_action_fallback=json_fallback,
    )
    return AdapterInfo(adapter=adapter, backend="ollama", model_name=model)


def _build_anthropic(cfg: dict) -> AdapterInfo:
    from verifiable_observability.agent.anthropic_adapter import AnthropicAgentAdapter

    ant_cfg = cfg.get("anthropic", {})
    model = os.environ.get("VO_MODEL") or ant_cfg.get("model", "claude-3-5-sonnet-20241022")
    max_tokens = int(ant_cfg.get("max_tokens", 1024))
    temperature = float(ant_cfg.get("temperature", 0))

    adapter = AnthropicAgentAdapter(model=model, max_tokens=max_tokens, temperature=temperature)
    return AdapterInfo(adapter=adapter, backend="anthropic", model_name=model)


def _build_openai(cfg: dict) -> AdapterInfo:
    from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

    oai_cfg = cfg.get("openai", {})
    model = os.environ.get("VO_OPENAI_MODEL") or oai_cfg.get("model", "gpt-4o")
    max_tokens = int(oai_cfg.get("max_tokens", 1024))
    temperature = float(oai_cfg.get("temperature", 0))

    adapter = OpenAIAgentAdapter(model=model, max_tokens=max_tokens, temperature=temperature)
    return AdapterInfo(adapter=adapter, backend="openai", model_name=model)

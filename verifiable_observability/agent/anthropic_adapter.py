"""
Anthropic Claude Adapter — Phase 4 real LLM wiring.

Wraps the Anthropic Messages API to implement AgentAdapterBase.

Features:
  - Tool-use support (claude-3-5-sonnet / claude-3-7-sonnet)
  - Automatic retry with exponential back-off (rate-limit / 529 errors)
  - Tool result injection into conversation history (Anthropic multi-turn format)
  - Graceful fallback: if the model returns a text-only response (no tool call),
    the reasoning is captured and is_final is set to True so the orchestrator
    can terminate cleanly.

Environment variables (loaded via python-dotenv or os.environ):
  ANTHROPIC_API_KEY   required
  VO_MODEL            optional, default "claude-3-5-sonnet-20241022"
  VO_MAX_TOKENS       optional, default 1024
  VO_TEMPERATURE      optional, default 0 (deterministic)

Usage::

    from verifiable_observability.agent.anthropic_adapter import AnthropicAgentAdapter
    adapter = AnthropicAgentAdapter()           # reads env
    # or
    adapter = AnthropicAgentAdapter(api_key="sk-ant-...", model="claude-3-5-sonnet-20241022")
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from dotenv import load_dotenv

from verifiable_observability.agent.adapter import AgentAdapterBase
from verifiable_observability.agent.tool_registry import FINANCE_TOOLS_ANTHROPIC
from verifiable_observability.storage.models import AgentResponse, Task

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TEMPERATURE = 0
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


class AnthropicAgentAdapter(AgentAdapterBase):
    """
    Live Anthropic Claude adapter.

    Args:
        api_key:     Anthropic API key.  Falls back to ANTHROPIC_API_KEY env var.
        model:       Model ID.  Falls back to VO_MODEL env var, then default.
        max_tokens:  Max completion tokens.  Falls back to VO_MAX_TOKENS.
        temperature: Sampling temperature.  Falls back to VO_TEMPERATURE.
        tools:       Tool schema list (Anthropic format).  Defaults to Finance tools.
        max_retries: Number of retries on transient API errors.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        try:
            import anthropic as _anthropic  # noqa: F401 — validate import early
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicAgentAdapter. "
                "Install it with: pip install anthropic"
            ) from exc

        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Anthropic API key not found. "
                "Set ANTHROPIC_API_KEY env var or pass api_key= to the constructor."
            )

        self.model = model or os.environ.get("VO_MODEL", _DEFAULT_MODEL)
        self.max_tokens = max_tokens or int(
            os.environ.get("VO_MAX_TOKENS", str(_DEFAULT_MAX_TOKENS))
        )
        self.temperature = temperature if temperature is not None else float(
            os.environ.get("VO_TEMPERATURE", str(_DEFAULT_TEMPERATURE))
        )
        self.tools = tools if tools is not None else FINANCE_TOOLS_ANTHROPIC
        self.max_retries = max_retries

        import anthropic

        self._client = anthropic.Anthropic(api_key=self._api_key)
        logger.info(
            "AnthropicAgentAdapter initialised | model=%s max_tokens=%d temperature=%.2f",
            self.model,
            self.max_tokens,
            self.temperature,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(
        self,
        system_prompt: str,
        conversation: list[dict[str, Any]],
        task: Task,
    ) -> AgentResponse:
        """
        Call the Anthropic Messages API and parse the response.

        The conversation list follows the Anthropic multi-turn format:
            [{"role": "user"|"assistant"|"tool", "content": ...}, ...]

        Tool result messages are automatically re-formatted to Anthropic's
        expected structure ({"role": "user", "content": [{"type": "tool_result", ...}]}).
        """
        messages = self._build_messages(conversation, task)
        raw_response = self._call_with_retry(system_prompt, messages)
        return self._parse_response(raw_response)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        conversation: list[dict[str, Any]],
        task: Task,
    ) -> list[dict[str, Any]]:
        """
        Convert the orchestrator's flat conversation list to Anthropic format.

        The orchestrator appends:
          - {"role": "assistant", "content": <raw_text>}
          - {"role": "tool",      "content": <tool_result_str>}

        Anthropic expects tool results as user messages:
          {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}

        Since the orchestrator doesn't carry tool_use_ids, we synthesise them.
        """
        messages: list[dict[str, Any]] = []

        # Inject the task as the opening user message if conversation is empty
        if not conversation:
            messages.append(
                {
                    "role": "user",
                    "content": task.description,
                }
            )
            return messages

        pending_tool_use_id: str | None = None

        for msg in conversation:
            role = msg["role"]
            content = msg["content"]

            if role == "assistant":
                # Pass through; store last tool_use_id if present
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            pending_tool_use_id = block.get("id", "tool-0")
                messages.append({"role": "assistant", "content": content})

            elif role == "tool":
                # Re-wrap as Anthropic tool_result inside a user message
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": pending_tool_use_id or "tool-0",
                                "content": str(content),
                            }
                        ],
                    }
                )
                pending_tool_use_id = None

            else:
                # user messages pass through
                messages.append({"role": role, "content": content})

        # Ensure the conversation ends with a user turn
        if messages and messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": "Continue."})

        return messages

    def _call_with_retry(
        self, system_prompt: str, messages: list[dict[str, Any]]
    ) -> Any:
        """Call the Anthropic API with exponential back-off on transient errors."""
        import anthropic

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=system_prompt,
                    messages=messages,
                    tools=self.tools,
                )
                logger.debug(
                    "Anthropic API call succeeded | attempt=%d stop_reason=%s",
                    attempt + 1,
                    response.stop_reason,
                )
                return response

            except anthropic.RateLimitError as exc:
                wait = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Rate limited by Anthropic (attempt %d/%d). Retrying in %.1fs.",
                    attempt + 1,
                    self.max_retries,
                    wait,
                )
                time.sleep(wait)
                last_exc = exc

            except anthropic.APIStatusError as exc:
                # 529 = overloaded; other 5xx = server error — retry
                if exc.status_code in (429, 500, 502, 503, 529):
                    wait = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Anthropic API error %d (attempt %d/%d). Retrying in %.1fs.",
                        exc.status_code,
                        attempt + 1,
                        self.max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    last_exc = exc
                else:
                    raise  # non-retryable error

        raise RuntimeError(
            f"Anthropic API call failed after {self.max_retries} retries."
        ) from last_exc

    @staticmethod
    def _parse_response(response: Any) -> AgentResponse:
        """
        Parse an Anthropic Messages response into an AgentResponse.

        Handles two stop reasons:
          - "tool_use"   : extract tool name + inputs
          - "end_turn"   : text-only response → is_final=True
        """
        text_parts: list[str] = []
        tool_name: str | None = None
        tool_parameters: dict[str, Any] = {}

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_name = block.name
                tool_parameters = dict(block.input) if block.input else {}

        reasoning = " ".join(text_parts).strip()
        is_final = response.stop_reason == "end_turn" and tool_name is None

        logger.debug(
            "Parsed response | tool=%s is_final=%s reasoning_len=%d",
            tool_name,
            is_final,
            len(reasoning),
        )

        return AgentResponse(
            reasoning=reasoning or "(no reasoning text)",
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            is_final=is_final,
            raw_text=" ".join(text_parts),
        )

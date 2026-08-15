"""
OpenAI GPT Adapter — Phase 4 real LLM wiring.

Wraps the OpenAI Chat Completions API (function-calling / tool-calls) to
implement AgentAdapterBase.

Features:
  - Tool/function-calling support (gpt-4o, gpt-4-turbo, gpt-3.5-turbo-0125)
  - Automatic retry with exponential back-off (rate-limit / server errors)
  - Tool result injection into conversation history (OpenAI multi-turn format)
  - Graceful fallback: if the model returns a text-only response (no tool call),
    the reasoning is captured and is_final is set to True.

Environment variables (loaded via python-dotenv or os.environ):
  OPENAI_API_KEY      required
  VO_OPENAI_MODEL     optional, default "gpt-4o"
  VO_MAX_TOKENS       optional, default 1024
  VO_TEMPERATURE      optional, default 0 (deterministic)

Usage::

    from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter
    adapter = OpenAIAgentAdapter()              # reads env
    # or
    adapter = OpenAIAgentAdapter(api_key="sk-...", model="gpt-4o")
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv

from verifiable_observability.agent.adapter import AgentAdapterBase
from verifiable_observability.agent.tool_registry import FINANCE_TOOLS_OPENAI
from verifiable_observability.storage.models import AgentResponse, Task

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o"
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TEMPERATURE = 0
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


class OpenAIAgentAdapter(AgentAdapterBase):
    """
    Live OpenAI GPT adapter using tool-calling.

    Args:
        api_key:     OpenAI API key.  Falls back to OPENAI_API_KEY env var.
        model:       Model ID.  Falls back to VO_OPENAI_MODEL env var, then default.
        max_tokens:  Max completion tokens.  Falls back to VO_MAX_TOKENS.
        temperature: Sampling temperature.  Falls back to VO_TEMPERATURE.
        tools:       Tool schema list (OpenAI format).  Defaults to Finance tools.
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
            import openai as _openai  # noqa: F401 — validate import early
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OpenAIAgentAdapter. "
                "Install it with: pip install openai"
            ) from exc

        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key not found. "
                "Set OPENAI_API_KEY env var or pass api_key= to the constructor."
            )

        self.model = model or os.environ.get("VO_OPENAI_MODEL", _DEFAULT_MODEL)
        self.max_tokens = max_tokens or int(
            os.environ.get("VO_MAX_TOKENS", str(_DEFAULT_MAX_TOKENS))
        )
        self.temperature = temperature if temperature is not None else float(
            os.environ.get("VO_TEMPERATURE", str(_DEFAULT_TEMPERATURE))
        )
        self.tools = tools if tools is not None else FINANCE_TOOLS_OPENAI
        self.max_retries = max_retries

        import openai

        self._client = openai.OpenAI(api_key=self._api_key)
        logger.info(
            "OpenAIAgentAdapter initialised | model=%s max_tokens=%d temperature=%.2f",
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
        Call the OpenAI Chat Completions API and parse the response.

        The conversation history is built as:
          [{"role": "system", "content": system_prompt},
           {"role": "user",   "content": task.description},
           ... prior turns ...
           {"role": "assistant", "content": ..., "tool_calls": [...]},
           {"role": "tool",      "tool_call_id": ..., "content": ...}]
        """
        messages = self._build_messages(system_prompt, conversation, task)
        raw_response = self._call_with_retry(messages)
        return self._parse_response(raw_response)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        system_prompt: str,
        conversation: list[dict[str, Any]],
        task: Task,
    ) -> list[dict[str, Any]]:
        """
        Build the OpenAI messages list from the orchestrator's conversation log.

        The orchestrator appends:
          - {"role": "assistant", "content": <raw_text>}
          - {"role": "tool",      "content": <tool_result_str>}

        OpenAI expects tool messages paired with the assistant's tool_call_id.
        We synthesise IDs since the orchestrator doesn't propagate them.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        if not conversation:
            messages.append({"role": "user", "content": task.description})
            return messages

        pending_tool_call_id: str | None = None

        for msg in conversation:
            role = msg["role"]
            content = msg["content"]

            if role == "assistant":
                # Check if content already has tool_calls embedded (raw API pass-through)
                if isinstance(content, dict) and "tool_calls" in content:
                    tc_list = content["tool_calls"]
                    if tc_list:
                        pending_tool_call_id = tc_list[0].get("id", "call-0")
                    messages.append({"role": "assistant", **content})
                else:
                    messages.append({"role": "assistant", "content": content})

            elif role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": pending_tool_call_id or "call-0",
                        "content": str(content),
                    }
                )
                pending_tool_call_id = None

            else:
                messages.append({"role": role, "content": content})

        # Ensure conversation ends with a user turn if not already
        if messages[-1]["role"] not in ("user", "tool"):
            messages.append({"role": "user", "content": "Continue."})

        return messages

    def _call_with_retry(self, messages: list[dict[str, Any]]) -> Any:
        """Call the OpenAI Chat Completions API with exponential back-off."""
        import openai

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    messages=messages,
                    tools=self.tools,
                    tool_choice="auto",
                )
                finish_reason = response.choices[0].finish_reason
                logger.debug(
                    "OpenAI API call succeeded | attempt=%d finish_reason=%s",
                    attempt + 1,
                    finish_reason,
                )
                return response

            except openai.RateLimitError as exc:
                wait = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Rate limited by OpenAI (attempt %d/%d). Retrying in %.1fs.",
                    attempt + 1,
                    self.max_retries,
                    wait,
                )
                time.sleep(wait)
                last_exc = exc

            except openai.APIStatusError as exc:
                if exc.status_code in (429, 500, 502, 503):
                    wait = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "OpenAI API error %d (attempt %d/%d). Retrying in %.1fs.",
                        exc.status_code,
                        attempt + 1,
                        self.max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    last_exc = exc
                else:
                    raise  # non-retryable

        raise RuntimeError(
            f"OpenAI API call failed after {self.max_retries} retries."
        ) from last_exc

    @staticmethod
    def _parse_response(response: Any) -> AgentResponse:
        """
        Parse an OpenAI Chat Completions response into an AgentResponse.

        Handles two finish reasons:
          - "tool_calls"  : extract first tool call name + arguments
          - "stop"        : text-only → is_final=True
        """
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason

        reasoning_text: str = message.content or ""
        tool_name: str | None = None
        tool_parameters: dict[str, Any] = {}

        if finish_reason == "tool_calls" and message.tool_calls:
            tc = message.tool_calls[0]  # take the first tool call
            tool_name = tc.function.name
            try:
                tool_parameters = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse tool arguments as JSON: %s",
                    tc.function.arguments,
                )
                tool_parameters = {}

        is_final = (finish_reason == "stop") and tool_name is None

        logger.debug(
            "Parsed OpenAI response | tool=%s is_final=%s reasoning_len=%d",
            tool_name,
            is_final,
            len(reasoning_text),
        )

        return AgentResponse(
            reasoning=reasoning_text or "(no reasoning text)",
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            is_final=is_final,
            raw_text=reasoning_text,
        )

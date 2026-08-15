"""
Ollama Agent Adapter — Phase 4 local-model backend (default).

Wraps the Ollama OpenAI-compatible endpoint (http://localhost:11434/v1)
to implement AgentAdapterBase, making it a drop-in replacement for
AnthropicAgentAdapter / OpenAIAgentAdapter.

Features:
  - Native tool/function-calling via OpenAI-compatible API (when the model
    supports it — both llama3.2 and qwen2.5 advertise the "tools" capability)
  - JSON-action fallback: if tool-calling is unavailable or fails to parse,
    re-prompts the model to emit a structured JSON block and parses it into
    the same AgentResponse/Action schema — validated with one retry
  - Connection-error detection: raises OllamaUnavailableError with a clear
    actionable message (not a confusing downstream Rule Bank error)
  - Request timeout: configurable, default 120 s
  - Auto-discovery: probes http://localhost:11434/api/tags at init to confirm
    the requested model is pulled; suggests `ollama pull <model>` if not found
  - Config via env vars: AGENT_MODEL, OLLAMA_BASE_URL, OLLAMA_TIMEOUT

Environment variables:
  AGENT_MODEL        Model to use (e.g. llama3.2:3b, qwen2.5:14b)
  OLLAMA_BASE_URL    Override server URL (default: http://localhost:11434/v1)
  OLLAMA_TIMEOUT     Request timeout in seconds (default: 120)

Usage::

    from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter
    adapter = OllamaAgentAdapter()          # reads env / config.yaml defaults
    adapter = OllamaAgentAdapter(model="llama3.2:3b")
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from dotenv import load_dotenv

from verifiable_observability.agent.adapter import AgentAdapterBase
from verifiable_observability.agent.tool_registry import FINANCE_TOOLS_OPENAI
from verifiable_observability.storage.models import AgentResponse, Task

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_DEFAULT_MODEL = "llama3.2:3b"
_DEFAULT_TIMEOUT = 120
_MAX_RETRIES = 2

# Fallback prompt template injected when native tool-calling isn't used
_JSON_ACTION_PROMPT = """
You must respond with a JSON object (and nothing else outside the JSON block)
in the following format to indicate a tool call:

```json
{{
  "tool_name": "<name of the tool to call>",
  "parameters": {{<tool parameters as key-value pairs>}},
  "reasoning": "<brief explanation of why you are calling this tool>"
}}
```

If the task is complete and no further tool calls are needed, respond with:
```json
{{
  "tool_name": null,
  "parameters": {{}},
  "reasoning": "<summary of what was accomplished>"
}}
```

Available tools: {tool_names}
"""


class OllamaUnavailableError(RuntimeError):
    """Raised when the Ollama server cannot be reached."""


class OllamaModelNotFoundError(RuntimeError):
    """Raised when the requested model is not pulled locally."""


class OllamaAgentAdapter(AgentAdapterBase):
    """
    Local Ollama adapter — the default backend for Verifiable Observability.

    Args:
        base_url:            Ollama OpenAI-compatible endpoint.
        model:               Model tag (e.g. 'llama3.2:3b').  Falls back to
                             AGENT_MODEL env var, then 'llama3.2:3b'.
        timeout:             Per-request timeout in seconds.
        max_retries:         Retries on connection / parse errors.
        json_action_fallback: If True, fall back to JSON-prompt parsing when
                             native tool-calling response is missing/unparsable.
        tools:               Tool schema list (OpenAI format).  Defaults to
                             Finance tools from tool_registry.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_retries: int = _MAX_RETRIES,
        json_action_fallback: bool = True,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OllamaAgentAdapter "
                "(it's used for the OpenAI-compatible Ollama endpoint). "
                "Install it with: pip install openai"
            ) from exc

        self.base_url = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("AGENT_MODEL", _DEFAULT_MODEL)
        )
        self.timeout = timeout or int(
            os.environ.get("OLLAMA_TIMEOUT", str(_DEFAULT_TIMEOUT))
        )
        self.max_retries = max_retries
        self.json_action_fallback = json_action_fallback
        self.tools = tools if tools is not None else FINANCE_TOOLS_OPENAI

        # Validate server + model availability at construction time
        self._validate_server()
        self._validate_model()

        import openai as _openai

        self._client = _openai.OpenAI(
            api_key="ollama",           # Ollama ignores the key; any non-empty string works
            base_url=self.base_url,
            timeout=self.timeout,
        )

        logger.info(
            "OllamaAgentAdapter initialised | model=%s base_url=%s timeout=%ds",
            self.model,
            self.base_url,
            self.timeout,
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_server(self) -> None:
        """Probe the Ollama REST API; raise OllamaUnavailableError if unreachable."""
        import urllib.error
        import urllib.request

        # The /api/tags endpoint lives on the non-v1 port (11434 base)
        tags_url = self.base_url.replace("/v1", "") + "/api/tags"
        try:
            urllib.request.urlopen(tags_url, timeout=5)
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaUnavailableError(
                f"Cannot reach Ollama server at {tags_url}.\n"
                "Make sure Ollama is running:\n"
                "  Windows: start the Ollama app, or run `ollama serve` in a terminal\n"
                "  Linux/Mac: `ollama serve` (or it runs as a background service)\n"
                f"Original error: {exc}"
            ) from exc

    def _validate_model(self) -> None:
        """Check the model is pulled; raise OllamaModelNotFoundError with pull hint."""
        import urllib.request
        import json as _json

        tags_url = self.base_url.replace("/v1", "") + "/api/tags"
        try:
            resp = urllib.request.urlopen(tags_url, timeout=5)
            data = _json.loads(resp.read())
        except Exception:
            # Server already validated above; silently skip if this fails
            return

        available = {m["name"] for m in data.get("models", [])}
        # Accept both "llama3.2:3b" and partial match "llama3.2"
        if self.model not in available and not any(
            m.startswith(self.model.split(":")[0]) for m in available
        ):
            raise OllamaModelNotFoundError(
                f"Model '{self.model}' is not pulled locally.\n"
                f"Available models: {sorted(available) or '(none)'}\n"
                f"Pull it with:  ollama pull {self.model}\n"
                "Or set AGENT_MODEL to one of the available models."
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
        Call Ollama and parse the response into AgentResponse.

        Tries native tool-calling first; falls back to JSON-prompt mode
        if the response contains no tool calls and json_action_fallback=True.
        """
        messages = self._build_messages(system_prompt, conversation, task)

        for attempt in range(self.max_retries + 1):
            try:
                return self._try_native_tools(messages)
            except _NativeToolFailed as exc:
                if not self.json_action_fallback:
                    logger.warning(
                        "Native tool-calling failed and fallback is disabled: %s", exc
                    )
                    return AgentResponse(
                        reasoning=str(exc),
                        is_final=True,
                        raw_text=str(exc),
                    )
                logger.debug(
                    "Native tool-calling had no tool call (attempt %d/%d); "
                    "trying JSON-action fallback.",
                    attempt + 1,
                    self.max_retries + 1,
                )
                return self._try_json_fallback(messages)
            except _ParseError as exc:
                if attempt < self.max_retries:
                    logger.warning(
                        "JSON parse failed (attempt %d/%d): %s — retrying.",
                        attempt + 1,
                        self.max_retries + 1,
                        exc,
                    )
                    time.sleep(0.5)
                    continue
                logger.error("JSON parse failed after %d attempts.", self.max_retries + 1)
                return AgentResponse(
                    reasoning=f"Malformed action after {self.max_retries + 1} retries: {exc}",
                    is_final=True,
                    raw_text=str(exc),
                )
            except OllamaUnavailableError:
                raise
            except Exception as exc:
                self._handle_connection_error(exc)

        # Should not reach here
        return AgentResponse(reasoning="Adapter exhausted retries.", is_final=True, raw_text="")

    # ------------------------------------------------------------------
    # Internal: native tool-calling path
    # ------------------------------------------------------------------

    def _try_native_tools(self, messages: list[dict[str, Any]]) -> AgentResponse:
        """
        Attempt a call using the OpenAI-compatible tools parameter.

        Raises _NativeToolFailed if the response contains no tool call
        (indicating the model didn't follow the tool-calling format).
        """
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
            )
        except Exception as exc:
            self._handle_connection_error(exc)

        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason

        text_content: str = message.content or ""

        if finish_reason == "tool_calls" and message.tool_calls:
            tc = message.tool_calls[0]
            tool_name = tc.function.name
            try:
                tool_params = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.warning("Malformed tool arguments JSON: %s", tc.function.arguments)
                tool_params = {}

            logger.debug(
                "Native tool call | tool=%s params=%s", tool_name, tool_params
            )
            return AgentResponse(
                reasoning=text_content or f"Calling {tool_name}",
                tool_name=tool_name,
                tool_parameters=tool_params,
                is_final=False,
                raw_text=text_content,
            )

        if finish_reason == "stop":
            # Model finished without a tool call — task complete
            return AgentResponse(
                reasoning=text_content or "(no text)",
                is_final=True,
                raw_text=text_content,
            )

        # Neither tool_calls nor stop → no actionable response
        raise _NativeToolFailed(
            f"finish_reason={finish_reason!r}, no tool_calls in response"
        )

    # ------------------------------------------------------------------
    # Internal: JSON-action fallback path
    # ------------------------------------------------------------------

    def _try_json_fallback(self, messages: list[dict[str, Any]]) -> AgentResponse:
        """
        Re-prompt the model asking it to emit a JSON block describing its action.
        Parse the response and return an AgentResponse.
        """
        tool_names = [t["function"]["name"] for t in self.tools]
        fallback_prompt = _JSON_ACTION_PROMPT.format(tool_names=", ".join(tool_names))

        # Append the fallback instruction as a user message
        fallback_messages = messages + [
            {"role": "user", "content": fallback_prompt}
        ]

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=fallback_messages,
                # No tools= parameter — we want raw text with embedded JSON
            )
        except Exception as exc:
            self._handle_connection_error(exc)

        raw = response.choices[0].message.content or ""
        logger.debug("JSON fallback raw response: %.200s", raw)

        return self._parse_json_action(raw)

    @staticmethod
    def _parse_json_action(raw_text: str) -> AgentResponse:
        """
        Extract and validate the JSON action block from the model's text output.

        Looks for a ```json ... ``` fenced block first, then falls back to
        scanning for the first { ... } object in the text.

        Raises _ParseError on failure.
        """
        # 1. Try fenced ```json block
        fence_match = re.search(r"```json\s*(.*?)```", raw_text, re.DOTALL | re.IGNORECASE)
        json_str = fence_match.group(1).strip() if fence_match else None

        # 2. Fall back to first { ... } in text
        if not json_str:
            brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if brace_match:
                json_str = brace_match.group(0).strip()

        if not json_str:
            raise _ParseError(f"No JSON block found in response: {raw_text[:200]!r}")

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise _ParseError(f"JSON decode error: {exc} | input: {json_str[:200]!r}") from exc

        # Validate required fields
        if not isinstance(data, dict):
            raise _ParseError(f"Expected JSON object, got: {type(data).__name__}")

        tool_name: str | None = data.get("tool_name") or None
        parameters: dict[str, Any] = data.get("parameters") or {}
        reasoning: str = data.get("reasoning") or raw_text

        if not isinstance(parameters, dict):
            parameters = {}

        is_final = tool_name is None
        logger.debug(
            "JSON fallback parsed | tool=%s is_final=%s", tool_name, is_final
        )
        return AgentResponse(
            reasoning=reasoning,
            tool_name=tool_name,
            tool_parameters=parameters,
            is_final=is_final,
            raw_text=raw_text,
        )

    # ------------------------------------------------------------------
    # Internal: message construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        system_prompt: str,
        conversation: list[dict[str, Any]],
        task: Task,
    ) -> list[dict[str, Any]]:
        """Build OpenAI-format messages list for Ollama."""
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
                if isinstance(content, dict) and "tool_calls" in content:
                    tc_list = content.get("tool_calls", [])
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

        # Ensure the thread ends with a user turn
        if messages[-1]["role"] not in ("user", "tool"):
            messages.append({"role": "user", "content": "Continue."})

        return messages

    # ------------------------------------------------------------------
    # Internal: error handling
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_connection_error(exc: Exception) -> None:
        """
        Translate connection-level exceptions into OllamaUnavailableError
        so they surface clearly before hitting Rule Bank / CCM / Metrics code.
        """
        import openai

        err_str = str(exc).lower()
        if (
            isinstance(exc, openai.APIConnectionError)
            or "connection" in err_str
            or "refused" in err_str
            or "unreachable" in err_str
            or "timeout" in err_str
        ):
            raise OllamaUnavailableError(
                "Lost connection to Ollama server during a request.\n"
                "Check that `ollama serve` is still running and try again.\n"
                f"Original error: {exc}"
            ) from exc
        raise exc


# ---------------------------------------------------------------------------
# Private sentinel exceptions (not exported)
# ---------------------------------------------------------------------------


class _NativeToolFailed(Exception):
    """Raised internally when a native tool-call response has no tool calls."""


class _ParseError(Exception):
    """Raised internally when JSON-action parsing fails."""

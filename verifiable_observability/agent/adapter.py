"""
Agent Adapter — abstract interface for LLM backends.

Implementations:
  - ScriptedAgentAdapter   : pre-scripted responses (Phase 0/1, no API calls)
  - AnthropicAgentAdapter  : Anthropic Claude (Phase 4)
  - OpenAIAgentAdapter     : OpenAI GPT (Phase 4, alternative)

The interface is intentionally minimal so the orchestrator never needs to know
which backend is active.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Any

from verifiable_observability.storage.models import AgentResponse, Task


class AgentAdapterBase(ABC):
    """
    Abstract adapter interface for LLM backends.

    All adapters must implement generate(), which takes the current
    conversation state and returns an AgentResponse.
    """

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        conversation: list[dict[str, Any]],
        task: Task,
    ) -> AgentResponse:
        """
        Generate the next agent response.

        Args:
            system_prompt:  The orchestrator's system-level instructions.
            conversation:   List of {"role": ..., "content": ...} dicts
                            representing the turn history so far.
            task:           The active Task (for additional context).

        Returns:
            AgentResponse with reasoning, optional tool call, and is_final flag.
        """
        ...


class ScriptedAgentAdapter(AgentAdapterBase):
    """
    Scripted (deterministic) agent adapter for testing and smoke runs.

    Provide a list of AgentResponse objects at construction time.
    Each call to generate() pops the next response from the queue.
    When the queue is exhausted, returns a final "task complete" response.

    Example::

        adapter = ScriptedAgentAdapter([
            AgentResponse(
                reasoning="I need to check the portfolio balance first.",
                tool_name="get_portfolio",
                tool_parameters={"account_id": "ACC-001"},
                raw_text="<thinking>Check balance</thinking>",
            ),
            AgentResponse(
                reasoning="Balance confirmed. Task complete.",
                is_final=True,
                raw_text="Task complete.",
            ),
        ])
    """

    def __init__(self, script: list[AgentResponse]) -> None:
        self._script: list[AgentResponse] = list(script)
        self._index = 0

    def generate(
        self,
        system_prompt: str,
        conversation: list[dict[str, Any]],
        task: Task,
    ) -> AgentResponse:
        if self._index < len(self._script):
            response = copy.deepcopy(self._script[self._index])
            self._index += 1
            return response

        # Script exhausted — signal task complete
        return AgentResponse(
            reasoning="Script exhausted. Signaling task complete.",
            is_final=True,
            raw_text="[script exhausted]",
        )

    def reset(self) -> None:
        """Reset the script pointer to the beginning."""
        self._index = 0

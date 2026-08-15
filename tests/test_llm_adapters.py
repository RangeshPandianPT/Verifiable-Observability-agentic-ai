"""
Phase 4 — Unit tests for AnthropicAgentAdapter and OpenAIAgentAdapter.

All tests use mocks; no real API calls are made.  Tests cover:
  - Adapter initialisation (env var / explicit key)
  - Successful tool-use parsing
  - Text-only (end_turn / stop) → is_final=True
  - Retry logic on rate-limit errors
  - Message construction helpers
  - Tool registry structure sanity
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from verifiable_observability.agent.tool_registry import (
    FINANCE_TOOLS_ANTHROPIC,
    FINANCE_TOOLS_OPENAI,
    simulate_tool_call,
)
from verifiable_observability.storage.models import AgentResponse, Domain, Task


# ---------------------------------------------------------------------------
# Helpers — fake Anthropic response objects
# ---------------------------------------------------------------------------


def _make_anthropic_tool_response(tool_name: str, tool_input: dict) -> MagicMock:
    """Build a fake Anthropic Messages response with a tool_use block."""
    text_block = SimpleNamespace(type="text", text="I will check the balance first.")
    tool_block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)
    response = MagicMock()
    response.content = [text_block, tool_block]
    response.stop_reason = "tool_use"
    return response


def _make_anthropic_text_response(text: str) -> MagicMock:
    """Build a fake Anthropic Messages response with text only (end_turn)."""
    text_block = SimpleNamespace(type="text", text=text)
    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    return response


# ---------------------------------------------------------------------------
# Helpers — fake OpenAI response objects
# ---------------------------------------------------------------------------


def _make_openai_tool_response(tool_name: str, arguments: dict) -> MagicMock:
    """Build a fake OpenAI Chat Completions response with a tool_call."""
    tc = MagicMock()
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments)

    message = MagicMock()
    message.content = None
    message.tool_calls = [tc]

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    return response


def _make_openai_text_response(text: str) -> MagicMock:
    """Build a fake OpenAI Chat Completions text-only response."""
    message = MagicMock()
    message.content = text
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"

    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# Sample task
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_task() -> Task:
    return Task(
        domain=Domain.FINANCE,
        description="Transfer $500 from ACC-001 to ACC-002.",
        metadata={"amount_usd": 500},
    )


# ===========================================================================
# Tool Registry tests
# ===========================================================================


class TestToolRegistry:
    def test_anthropic_tools_non_empty(self):
        assert len(FINANCE_TOOLS_ANTHROPIC) > 0

    def test_anthropic_tools_have_required_fields(self):
        for tool in FINANCE_TOOLS_ANTHROPIC:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_openai_tools_non_empty(self):
        assert len(FINANCE_TOOLS_OPENAI) > 0

    def test_openai_tools_wrapped_correctly(self):
        for tool in FINANCE_TOOLS_OPENAI:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_tool_counts_match(self):
        assert len(FINANCE_TOOLS_ANTHROPIC) == len(FINANCE_TOOLS_OPENAI)

    def test_simulate_tool_call_known_tool(self):
        result = simulate_tool_call("get_account_balance", {"account_id": "ACC-001"})
        assert result["status"] == "ok"
        assert result["simulated"] is True
        assert result["tool"] == "get_account_balance"

    def test_simulate_tool_call_unknown_tool(self):
        result = simulate_tool_call("mystery_tool", {})
        assert result["status"] == "ok"
        assert result["unknown_tool"] is True

    def test_simulate_tool_call_echoes_input(self):
        params = {"account_id": "ACC-999"}
        result = simulate_tool_call("get_account_balance", params)
        assert result["input"] == params


# ===========================================================================
# AnthropicAgentAdapter tests
# ===========================================================================


class TestAnthropicAgentAdapter:
    """All Anthropic API calls are mocked."""

    @pytest.fixture()
    def adapter(self):
        """Create adapter with a fake API key."""
        with patch("anthropic.Anthropic"):
            from verifiable_observability.agent.anthropic_adapter import (
                AnthropicAgentAdapter,
            )

            return AnthropicAgentAdapter(api_key="sk-ant-fake-key-for-tests")

    def test_init_missing_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            import importlib

            # Remove cached env after clearing
            with patch("anthropic.Anthropic"):
                from verifiable_observability.agent.anthropic_adapter import (
                    AnthropicAgentAdapter,
                )

                with pytest.raises(ValueError, match="API key"):
                    AnthropicAgentAdapter(api_key="")

    def test_parse_tool_use_response(self, adapter):
        fake_resp = _make_anthropic_tool_response(
            "get_account_balance", {"account_id": "ACC-001"}
        )
        result = adapter._parse_response(fake_resp)

        assert isinstance(result, AgentResponse)
        assert result.tool_name == "get_account_balance"
        assert result.tool_parameters == {"account_id": "ACC-001"}
        assert result.is_final is False
        assert "balance" in result.reasoning.lower() or len(result.reasoning) > 0

    def test_parse_end_turn_sets_is_final(self, adapter):
        fake_resp = _make_anthropic_text_response("Task complete. No further actions needed.")
        result = adapter._parse_response(fake_resp)

        assert result.is_final is True
        assert result.tool_name is None
        assert "Task complete" in result.reasoning

    def test_build_messages_empty_conversation(self, sample_task):
        from verifiable_observability.agent.anthropic_adapter import AnthropicAgentAdapter

        messages = AnthropicAgentAdapter._build_messages([], sample_task)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert sample_task.description in messages[0]["content"]

    def test_build_messages_wraps_tool_result(self, sample_task):
        from verifiable_observability.agent.anthropic_adapter import AnthropicAgentAdapter

        conversation = [
            {"role": "assistant", "content": "Checking balance..."},
            {"role": "tool", "content": '{"balance": 5000}'},
        ]
        messages = AnthropicAgentAdapter._build_messages(conversation, sample_task)

        # Find the tool_result message
        tool_result_msgs = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and any(c.get("type") == "tool_result" for c in m["content"])
        ]
        assert len(tool_result_msgs) == 1
        assert tool_result_msgs[0]["role"] == "user"

    def test_generate_calls_api_and_returns_response(self, adapter, sample_task):
        fake_resp = _make_anthropic_tool_response(
            "execute_transfer", {"from_account": "ACC-001", "to_account": "ACC-002", "amount_usd": 500}
        )
        adapter._client.messages.create.return_value = fake_resp

        result = adapter.generate(
            system_prompt="You are a finance agent.",
            conversation=[],
            task=sample_task,
        )

        assert result.tool_name == "execute_transfer"
        assert result.tool_parameters["amount_usd"] == 500

    def test_retry_on_rate_limit(self, adapter, sample_task):
        import anthropic

        success_resp = _make_anthropic_text_response("Done.")
        adapter._client.messages.create.side_effect = [
            anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429),
                body={},
            ),
            success_resp,
        ]

        with patch("time.sleep"):  # Don't actually wait in tests
            result = adapter.generate(
                system_prompt="test",
                conversation=[],
                task=sample_task,
            )

        assert result.is_final is True
        assert adapter._client.messages.create.call_count == 2


# ===========================================================================
# OpenAIAgentAdapter tests
# ===========================================================================


class TestOpenAIAgentAdapter:
    """All OpenAI API calls are mocked."""

    @pytest.fixture()
    def adapter(self):
        with patch("openai.OpenAI"):
            from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

            return OpenAIAgentAdapter(api_key="sk-fake-key-for-tests")

    def test_init_missing_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("openai.OpenAI"):
                from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

                with pytest.raises(ValueError, match="API key"):
                    OpenAIAgentAdapter(api_key="")

    def test_parse_tool_call_response(self, adapter):
        fake_resp = _make_openai_tool_response(
            "get_portfolio_positions", {"portfolio_id": "PORT-001"}
        )
        result = adapter._parse_response(fake_resp)

        assert result.tool_name == "get_portfolio_positions"
        assert result.tool_parameters == {"portfolio_id": "PORT-001"}
        assert result.is_final is False

    def test_parse_stop_sets_is_final(self, adapter):
        fake_resp = _make_openai_text_response("I have completed the task.")
        result = adapter._parse_response(fake_resp)

        assert result.is_final is True
        assert result.tool_name is None
        assert "completed" in result.reasoning

    def test_build_messages_includes_system(self, sample_task):
        from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

        messages = OpenAIAgentAdapter._build_messages("System prompt here.", [], sample_task)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "System prompt here."

    def test_build_messages_empty_conversation_adds_task(self, sample_task):
        from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

        messages = OpenAIAgentAdapter._build_messages("sys", [], sample_task)
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any(sample_task.description in m["content"] for m in user_msgs)

    def test_build_messages_wraps_tool_result(self, sample_task):
        from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

        conversation = [
            {"role": "assistant", "content": "Calling get_account_balance..."},
            {"role": "tool", "content": '{"balance_usd": 5000}'},
        ]
        messages = OpenAIAgentAdapter._build_messages("sys", conversation, sample_task)

        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "tool_call_id" in tool_msgs[0]

    def test_generate_calls_api_and_returns_response(self, adapter, sample_task):
        fake_resp = _make_openai_tool_response(
            "execute_transfer",
            {"from_account": "ACC-001", "to_account": "ACC-002", "amount_usd": 500},
        )
        adapter._client.chat.completions.create.return_value = fake_resp

        result = adapter.generate(
            system_prompt="Finance agent.",
            conversation=[],
            task=sample_task,
        )

        assert result.tool_name == "execute_transfer"
        assert result.tool_parameters["amount_usd"] == 500

    def test_retry_on_rate_limit(self, adapter, sample_task):
        import openai

        success_resp = _make_openai_text_response("Done.")
        adapter._client.chat.completions.create.side_effect = [
            openai.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429),
                body={},
            ),
            success_resp,
        ]

        with patch("time.sleep"):
            result = adapter.generate(
                system_prompt="test",
                conversation=[],
                task=sample_task,
            )

        assert result.is_final is True
        assert adapter._client.chat.completions.create.call_count == 2

    def test_parse_invalid_json_arguments(self, adapter):
        """Adapter must not crash on malformed JSON tool arguments."""
        tc = MagicMock()
        tc.function.name = "get_account_balance"
        tc.function.arguments = "NOT VALID JSON {"

        message = MagicMock()
        message.content = None
        message.tool_calls = [tc]

        choice = MagicMock()
        choice.message = message
        choice.finish_reason = "tool_calls"

        response = MagicMock()
        response.choices = [choice]

        result = adapter._parse_response(response)
        assert result.tool_name == "get_account_balance"
        assert result.tool_parameters == {}  # graceful fallback

"""
Phase 4 (Ollama) — Unit tests for OllamaAgentAdapter and adapter factory.

All tests use mocks — no live Ollama server required.  Tests cover:

OllamaAgentAdapter:
  - Native tool-calling path (successful)
  - stop finish_reason → is_final=True (no tool call)
  - JSON-action fallback: fenced ```json``` block parsing
  - JSON-action fallback: bare { } brace scanning
  - JSON-action fallback: tool_name=null → is_final=True
  - One retry on parse failure before returning error response
  - _parse_json_action: valid fenced block
  - _parse_json_action: bare braces fallback
  - _parse_json_action: null tool_name → is_final
  - _parse_json_action: no JSON in text → ParseError
  - _parse_json_action: malformed JSON → ParseError
  - OllamaUnavailableError on connection refused
  - Message construction (empty conversation, tool result wrapping)

Adapter factory:
  - build_adapter("ollama") selects OllamaAgentAdapter
  - build_adapter("anthropic") selects AnthropicAgentAdapter
  - build_adapter("openai") selects OpenAIAgentAdapter
  - Unknown backend raises ValueError

Orchestrator integration:
  - agent_backend + model_name stamped on Trajectory
  - Rule Bank / CCM / Metrics code paths NOT touched by adapter swap
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from verifiable_observability.storage.models import (
    AgentResponse,
    ComplianceDecision,
    Domain,
    Task,
    TrajectoryOutcome,
)


# ---------------------------------------------------------------------------
# Helpers — fake OpenAI-compatible response objects (Ollama uses same format)
# ---------------------------------------------------------------------------


def _make_tool_response(tool_name: str, arguments: dict) -> MagicMock:
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


def _make_text_response(text: str, finish_reason: str = "stop") -> MagicMock:
    message = MagicMock()
    message.content = text
    message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# Sample task fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_task() -> Task:
    return Task(
        domain=Domain.FINANCE,
        description="Transfer $500 from ACC-001 to ACC-002.",
        metadata={"amount_usd": 500},
    )


# ===========================================================================
# OllamaAgentAdapter unit tests (server + model checks mocked out)
# ===========================================================================


@pytest.fixture()
def adapter():
    """OllamaAgentAdapter with server/model validation skipped."""
    with (
        patch("verifiable_observability.agent.ollama_adapter.OllamaAgentAdapter._validate_server"),
        patch("verifiable_observability.agent.ollama_adapter.OllamaAgentAdapter._validate_model"),
        patch("openai.OpenAI"),
    ):
        from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter

        adp = OllamaAgentAdapter(model="llama3.2:3b")
        # Replace client with mock after construction
        adp._client = MagicMock()
        return adp


class TestOllamaAdapterNativeTools:
    def test_native_tool_call_parsed(self, adapter, sample_task):
        adapter._client.chat.completions.create.return_value = _make_tool_response(
            "get_account_balance", {"account_id": "ACC-001"}
        )
        result = adapter.generate("sys", [], sample_task)

        assert result.tool_name == "get_account_balance"
        assert result.tool_parameters == {"account_id": "ACC-001"}
        assert result.is_final is False

    def test_stop_finish_reason_is_final(self, adapter, sample_task):
        adapter._client.chat.completions.create.return_value = _make_text_response(
            "The task is complete."
        )
        result = adapter.generate("sys", [], sample_task)

        assert result.is_final is True
        assert result.tool_name is None
        assert "complete" in result.reasoning.lower()


class TestOllamaAdapterJsonFallback:
    """Tests that exercise the JSON-action fallback path."""

    def test_fallback_parses_fenced_json_block(self, adapter, sample_task):
        """First call returns no tool_calls; fallback call returns JSON in a fence."""
        # First call: no tool call in response (triggers fallback)
        no_tool_resp = _make_text_response("Let me check the balance.", finish_reason="stop")
        no_tool_resp.choices[0].finish_reason = "length"  # not 'stop' → no tool

        fallback_raw = '```json\n{"tool_name": "get_account_balance", "parameters": {"account_id": "ACC-001"}, "reasoning": "Need balance"}\n```'
        fallback_resp = _make_text_response(fallback_raw)

        adapter._client.chat.completions.create.side_effect = [no_tool_resp, fallback_resp]

        result = adapter.generate("sys", [], sample_task)

        assert result.tool_name == "get_account_balance"
        assert result.tool_parameters["account_id"] == "ACC-001"
        assert result.is_final is False

    def test_fallback_parses_bare_braces(self, adapter, sample_task):
        """Fallback finds { } object even without a fenced block."""
        no_tool_resp = _make_text_response("Thinking...", finish_reason="length")
        bare_json = 'Sure! Here you go: {"tool_name": "execute_transfer", "parameters": {"from_account": "ACC-001", "to_account": "ACC-002", "amount_usd": 500}, "reasoning": "Transfer approved"}'
        fallback_resp = _make_text_response(bare_json)

        adapter._client.chat.completions.create.side_effect = [no_tool_resp, fallback_resp]

        result = adapter.generate("sys", [], sample_task)
        assert result.tool_name == "execute_transfer"
        assert result.tool_parameters["amount_usd"] == 500

    def test_fallback_null_tool_name_is_final(self, adapter, sample_task):
        """tool_name=null in JSON response → is_final=True."""
        no_tool_resp = _make_text_response("Done.", finish_reason="length")
        null_tool_json = '```json\n{"tool_name": null, "parameters": {}, "reasoning": "Task complete"}\n```'
        fallback_resp = _make_text_response(null_tool_json)

        adapter._client.chat.completions.create.side_effect = [no_tool_resp, fallback_resp]

        result = adapter.generate("sys", [], sample_task)
        assert result.is_final is True
        assert result.tool_name is None


class TestParseJsonAction:
    """Unit tests for the static _parse_json_action helper."""

    def setup_method(self):
        from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter

        self.parse = OllamaAgentAdapter._parse_json_action

    def test_fenced_block_extracted(self):
        raw = '```json\n{"tool_name": "foo", "parameters": {"x": 1}, "reasoning": "ok"}\n```'
        result = self.parse(raw)
        assert result.tool_name == "foo"
        assert result.tool_parameters == {"x": 1}
        assert result.is_final is False

    def test_bare_braces_fallback(self):
        raw = 'Here is my response: {"tool_name": "bar", "parameters": {}, "reasoning": "test"}'
        result = self.parse(raw)
        assert result.tool_name == "bar"

    def test_null_tool_name_sets_is_final(self):
        raw = '```json\n{"tool_name": null, "parameters": {}, "reasoning": "done"}\n```'
        result = self.parse(raw)
        assert result.is_final is True
        assert result.tool_name is None

    def test_no_json_raises_parse_error(self):
        from verifiable_observability.agent.ollama_adapter import _ParseError

        with pytest.raises(_ParseError, match="No JSON block"):
            self.parse("This is just plain text with no JSON at all.")

    def test_malformed_json_raises_parse_error(self):
        from verifiable_observability.agent.ollama_adapter import _ParseError

        with pytest.raises(_ParseError, match="JSON decode error"):
            self.parse("```json\n{broken json here\n```")

    def test_missing_reasoning_falls_back_to_raw(self):
        raw = '{"tool_name": "foo", "parameters": {}}'
        result = self.parse(raw)
        # reasoning should fall back to the raw text
        assert result.reasoning == raw


class TestOllamaMessageConstruction:
    def test_empty_conversation_injects_task(self, sample_task):
        from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter

        msgs = OllamaAgentAdapter._build_messages("sys prompt", [], sample_task)
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "sys prompt"
        assert any(
            m["role"] == "user" and sample_task.description in m["content"]
            for m in msgs
        )

    def test_tool_result_wrapped_with_id(self, sample_task):
        from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter

        conversation = [
            {"role": "assistant", "content": "Checking balance..."},
            {"role": "tool", "content": '{"balance_usd": 5000}'},
        ]
        msgs = OllamaAgentAdapter._build_messages("sys", conversation, sample_task)
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "tool_call_id" in tool_msgs[0]


class TestOllamaConnectionError:
    def test_unavailable_error_on_connection_refused(self, adapter, sample_task):
        import openai
        from verifiable_observability.agent.ollama_adapter import OllamaUnavailableError

        adapter._client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=MagicMock()
        )

        with pytest.raises(OllamaUnavailableError):
            adapter.generate("sys", [], sample_task)


# ===========================================================================
# Adapter Factory tests (server checks mocked to avoid requiring live Ollama)
# ===========================================================================


class TestAdapterFactory:
    def test_ollama_backend_selected(self):
        with (
            patch("verifiable_observability.agent.ollama_adapter.OllamaAgentAdapter._validate_server"),
            patch("verifiable_observability.agent.ollama_adapter.OllamaAgentAdapter._validate_model"),
            patch("openai.OpenAI"),
        ):
            from verifiable_observability.agent.factory import build_adapter
            from verifiable_observability.agent.ollama_adapter import OllamaAgentAdapter

            info = build_adapter("ollama")
            assert info.backend == "ollama"
            assert isinstance(info.adapter, OllamaAgentAdapter)

    def test_anthropic_backend_selected(self):
        with (
            patch("anthropic.Anthropic"),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-fake"}),
        ):
            from verifiable_observability.agent.factory import build_adapter
            from verifiable_observability.agent.anthropic_adapter import AnthropicAgentAdapter

            info = build_adapter("anthropic")
            assert info.backend == "anthropic"
            assert isinstance(info.adapter, AnthropicAgentAdapter)

    def test_openai_backend_selected(self):
        with (
            patch("openai.OpenAI"),
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-fake"}),
        ):
            from verifiable_observability.agent.factory import build_adapter
            from verifiable_observability.agent.openai_adapter import OpenAIAgentAdapter

            info = build_adapter("openai")
            assert info.backend == "openai"
            assert isinstance(info.adapter, OpenAIAgentAdapter)

    def test_unknown_backend_raises_value_error(self):
        from verifiable_observability.agent.factory import build_adapter

        with pytest.raises(ValueError, match="Unknown agent_backend"):
            build_adapter("magic_llm")

    def test_model_name_returned_in_info(self):
        with (
            patch("verifiable_observability.agent.ollama_adapter.OllamaAgentAdapter._validate_server"),
            patch("verifiable_observability.agent.ollama_adapter.OllamaAgentAdapter._validate_model"),
            patch("openai.OpenAI"),
        ):
            from verifiable_observability.agent.factory import build_adapter

            info = build_adapter("ollama")
            assert info.model_name  # non-empty


# ===========================================================================
# Orchestrator integration: backend/model stamped on Trajectory
# (Rule Bank, CCM, Metrics are NOT changed — assert they still work)
# ===========================================================================


class TestOrchestratorBackendProvenance:
    """
    Confirm that swapping the adapter only affects Trajectory.agent_backend
    and Trajectory.model_name — all other verification layers remain intact.
    """

    @pytest.fixture()
    def mock_ollama_adapter(self, sample_task):
        """A scripted adapter that pretends to be Ollama (no live server)."""
        from verifiable_observability.agent.adapter import ScriptedAgentAdapter

        return ScriptedAgentAdapter([
            AgentResponse(
                reasoning="I will check the balance.",
                tool_name="get_account_balance",
                tool_parameters={"account_id": "ACC-001"},
                raw_text="Turn 1",
            ),
            AgentResponse(
                reasoning="Balance confirmed. Task complete.",
                is_final=True,
                raw_text="Turn 2",
            ),
        ])

    def test_agent_backend_stamped_on_trajectory(self, mock_ollama_adapter, sample_task):
        from verifiable_observability.core.orchestrator import Orchestrator
        from verifiable_observability.core.strategy_profiler import StrategyProfiler
        from verifiable_observability.core.rule_bank import StubRuleBank
        from verifiable_observability.core.constraint_monitor import StubCCM
        from verifiable_observability.core.metrics import BasicMetricsEngine
        from verifiable_observability.storage.db import TrajectoryStore, create_db_engine

        engine = create_db_engine(":memory:")
        store = TrajectoryStore(engine)

        orch = Orchestrator(
            strategy_profiler=StrategyProfiler(),
            rule_bank=StubRuleBank(),
            ccm=StubCCM(),
            agent_adapter=mock_ollama_adapter,
            trajectory_store=store,
            metrics_engine=BasicMetricsEngine(),
            max_turns=5,
            agent_backend="ollama",
            model_name="llama3.2:3b",
        )

        trajectory = orch.run(sample_task)

        assert trajectory.agent_backend == "ollama"
        assert trajectory.model_name == "llama3.2:3b"
        assert trajectory.outcome == TrajectoryOutcome.COMPLETED

    def test_rule_bank_ccm_metrics_unchanged(self, mock_ollama_adapter, sample_task):
        """Swapping the adapter must not break Rule Bank checks or CCM decisions."""
        from verifiable_observability.core.orchestrator import Orchestrator
        from verifiable_observability.core.strategy_profiler import StrategyProfiler
        from verifiable_observability.core.rule_bank import StubRuleBank
        from verifiable_observability.core.constraint_monitor import StubCCM
        from verifiable_observability.core.metrics import BasicMetricsEngine
        from verifiable_observability.storage.db import TrajectoryStore, create_db_engine

        engine = create_db_engine(":memory:")
        store = TrajectoryStore(engine)

        orch = Orchestrator(
            strategy_profiler=StrategyProfiler(),
            rule_bank=StubRuleBank(),
            ccm=StubCCM(),
            agent_adapter=mock_ollama_adapter,
            trajectory_store=store,
            metrics_engine=BasicMetricsEngine(),
            max_turns=5,
            agent_backend="ollama",
            model_name="llama3.2:3b",
        )

        trajectory = orch.run(sample_task)

        # Rule Bank and CCM checks still happened
        for turn in trajectory.turns:
            assert len(turn.rule_checks) > 0, "Rule Bank check missing"
            if turn.actions:
                assert len(turn.constraint_checks) > 0, "CCM check missing"
                assert all(
                    cc.decision == ComplianceDecision.ALLOW
                    for cc in turn.constraint_checks
                ), "StubCCM should always ALLOW"

    def test_trajectory_persisted_with_backend_fields(self, mock_ollama_adapter, sample_task):
        """Backend provenance survives the SQLite round-trip."""
        from verifiable_observability.core.orchestrator import Orchestrator
        from verifiable_observability.core.strategy_profiler import StrategyProfiler
        from verifiable_observability.core.rule_bank import StubRuleBank
        from verifiable_observability.core.constraint_monitor import StubCCM
        from verifiable_observability.core.metrics import BasicMetricsEngine
        from verifiable_observability.storage.db import TrajectoryStore, create_db_engine

        engine = create_db_engine(":memory:")
        store = TrajectoryStore(engine)

        orch = Orchestrator(
            strategy_profiler=StrategyProfiler(),
            rule_bank=StubRuleBank(),
            ccm=StubCCM(),
            agent_adapter=mock_ollama_adapter,
            trajectory_store=store,
            metrics_engine=BasicMetricsEngine(),
            max_turns=5,
            agent_backend="ollama",
            model_name="llama3.2:3b",
        )

        trajectory = orch.run(sample_task)
        loaded = store.load(trajectory.trajectory_id)

        assert loaded is not None
        assert loaded.agent_backend == "ollama"
        assert loaded.model_name == "llama3.2:3b"

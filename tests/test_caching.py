"""Structural tests for prompt caching — no live LLM required.

Mocks client.messages.create and verifies that cache_control markers are
present in every API call after the caching implementation. These tests
prove structural correctness; cache_read_input_tokens in the Anthropic
response confirms functional correctness (observable only with real inference).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# report-writer directory has a hyphen so it can't be imported as a package;
# add it to sys.path to allow direct module import.
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "report-writer"))


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _end_turn_response(text: str = "ok") -> MagicMock:
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    block = MagicMock()
    block.text = text
    resp.content = [block]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 10
    resp.usage.cache_creation_input_tokens = 50
    resp.usage.cache_read_input_tokens = 0
    return resp


def _mock_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# ------------------------------------------------------------------ #
# shared/react.py                                                      #
# ------------------------------------------------------------------ #

class TestReactLoopCaching:

    async def test_system_prompt_wrapped_with_cache_control(self):
        from shared.react import react_loop

        client = _mock_client(_end_turn_response())
        await react_loop(
            client=client,
            system="You are a senior analyst",
            user_prompt="Analyze AAPL",
            tools=[],
            executors={},
            model="claude-sonnet-4-6",
        )

        system_arg = client.messages.create.call_args.kwargs["system"]
        assert isinstance(system_arg, list), "system must be a list of content blocks"
        assert system_arg[0]["type"] == "text"
        assert system_arg[0]["text"] == "You are a senior analyst"
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    async def test_initial_user_message_wrapped_with_cache_control(self):
        from shared.react import react_loop

        client = _mock_client(_end_turn_response())
        await react_loop(
            client=client,
            system="system",
            user_prompt="large context: fundamentals, news, rag",
            tools=[],
            executors={},
            model="claude-sonnet-4-6",
        )

        messages_arg = client.messages.create.call_args.kwargs["messages"]
        first = messages_arg[0]
        assert first["role"] == "user"
        assert isinstance(first["content"], list), "initial content must be a list of blocks"
        block = first["content"][0]
        assert block["type"] == "text"
        assert block["text"] == "large context: fundamentals, news, rag"
        assert block["cache_control"] == {"type": "ephemeral"}

    async def test_cache_control_present_on_every_react_turn(self):
        """cache_control on system prompt must survive across all ReAct turns."""
        from shared.react import react_loop

        tool_resp = MagicMock()
        tool_resp.stop_reason = "tool_use"
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "my_tool"
        tool_block.id = "tid_1"
        tool_block.input = {}
        tool_resp.content = [tool_block]
        tool_resp.usage.input_tokens = 200
        tool_resp.usage.output_tokens = 10
        tool_resp.usage.cache_creation_input_tokens = 100
        tool_resp.usage.cache_read_input_tokens = 0

        client = MagicMock()
        client.messages.create.side_effect = [tool_resp, _end_turn_response("final")]

        async def my_tool(_: dict) -> str:
            return "tool output"

        await react_loop(
            client=client,
            system="system",
            user_prompt="prompt",
            tools=[{"name": "my_tool", "description": "", "input_schema": {"type": "object", "properties": {}}}],
            executors={"my_tool": my_tool},
            model="claude-sonnet-4-6",
        )

        assert client.messages.create.call_count == 2
        for i, call in enumerate(client.messages.create.call_args_list):
            system_arg = call.kwargs["system"]
            assert system_arg[0]["cache_control"] == {"type": "ephemeral"}, \
                f"cache_control missing on turn {i + 1}"


# ------------------------------------------------------------------ #
# shared/llm_judge.py — pure function                                  #
# ------------------------------------------------------------------ #

class TestBuildUserContent:

    def test_rag_context_is_first_block_with_cache_control(self):
        from shared.llm_judge import _build_user_content

        blocks = _build_user_content(
            executive_summary="exec summary",
            report_dict={"candidati": []},
            news=[],
            fundamentals=[],
            rag_context="Investment policy: no crypto, no energy.",
        )

        assert "INTERNAL KNOWLEDGE BASE" in blocks[0]["text"]
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_report_json_block_has_no_cache_control(self):
        """Report JSON is the variable delta — must NOT have cache_control."""
        from shared.llm_judge import _build_user_content

        blocks = _build_user_content(
            executive_summary="exec",
            report_dict={"candidati": [{"ticker": "AAPL"}]},
            news=[],
            fundamentals=[],
            rag_context="policy",
        )

        report_block = next(b for b in blocks if "REPORT JSON" in b["text"])
        assert "cache_control" not in report_block

    def test_no_rag_context_produces_no_cached_block(self):
        """If rag_context is empty, no block with cache_control is emitted."""
        from shared.llm_judge import _build_user_content

        blocks = _build_user_content(
            executive_summary="exec",
            report_dict={},
            news=[],
            fundamentals=[],
            rag_context="",
        )

        for block in blocks:
            assert "cache_control" not in block


# ------------------------------------------------------------------ #
# shared/llm_judge.py — API call                                       #
# ------------------------------------------------------------------ #

class TestJudgeAPICaching:

    def _judge_client(self) -> MagicMock:
        client = MagicMock()
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='{"verdict":"PASS","grounding_score":90,"issues":[],"summary":"ok"}')],
            usage=MagicMock(
                input_tokens=50,
                output_tokens=20,
                cache_creation_input_tokens=40,
                cache_read_input_tokens=0,
            ),
        )
        return client

    async def test_system_prompt_has_cache_control(self):
        from shared.llm_judge import run_judge

        client = self._judge_client()
        await run_judge(
            client=client,
            executive_summary="Summary",
            report_dict={"candidati": []},
            news=[],
            fundamentals=[],
            rag_context="policy",
        )

        system_arg = client.messages.create.call_args.kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}

    async def test_user_content_is_structured_blocks(self):
        from shared.llm_judge import run_judge

        client = self._judge_client()
        await run_judge(
            client=client,
            executive_summary="Summary",
            report_dict={"candidati": []},
            news=[{"id": "N1"}],
            fundamentals=[{"ticker": "AAPL"}],
            rag_context="investment policy",
        )

        messages_arg = client.messages.create.call_args.kwargs["messages"]
        user_content = messages_arg[0]["content"]
        assert isinstance(user_content, list), "user content must be a list of blocks"
        assert all(isinstance(b, dict) and b.get("type") == "text" for b in user_content)


# ------------------------------------------------------------------ #
# agents/report-writer/report_writer.py                               #
# ------------------------------------------------------------------ #

class TestReportWriterCaching:

    def test_call_claude_system_has_cache_control(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="report content")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 200
        mock_response.usage.cache_creation_input_tokens = 80
        mock_response.usage.cache_read_input_tokens = 0

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import report_writer as rw
        with patch.object(rw, "get_llm_client", return_value=mock_client):
            rw._call_claude("my system prompt", "user message", "claude-sonnet-4-6", 1000)

        system_arg = mock_client.messages.create.call_args.kwargs["system"]
        assert isinstance(system_arg, list)
        assert system_arg[0]["type"] == "text"
        assert system_arg[0]["text"] == "my system prompt"
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}


# ------------------------------------------------------------------ #
# shared/react.py — session management (tool result size cap)         #
# ------------------------------------------------------------------ #

class TestReactLoopSessionManagement:

    def _tool_response(self, tool_name: str = "my_tool", tool_id: str = "tid_1") -> MagicMock:
        resp = MagicMock()
        resp.stop_reason = "tool_use"
        block = MagicMock()
        block.type = "tool_use"
        block.name = tool_name
        block.id = tool_id
        block.input = {}
        resp.content = [block]
        resp.usage.input_tokens = 200
        resp.usage.output_tokens = 10
        resp.usage.cache_creation_input_tokens = 100
        resp.usage.cache_read_input_tokens = 0
        return resp

    async def test_large_tool_result_is_truncated(self):
        """Tool results exceeding max_tool_result_chars must be capped."""
        from shared.react import react_loop

        client = MagicMock()
        client.messages.create.side_effect = [
            self._tool_response(),
            _end_turn_response("done"),
        ]

        large_output = "x" * 20_000

        async def big_tool(_: dict) -> str:
            return large_output

        cap = 500
        await react_loop(
            client=client,
            system="s",
            user_prompt="p",
            tools=[{"name": "my_tool", "description": "", "input_schema": {"type": "object", "properties": {}}}],
            executors={"my_tool": big_tool},
            model="claude-sonnet-4-6",
            max_tool_result_chars=cap,
        )

        second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
        tool_result_msg = second_call_messages[-1]
        tool_result_content = tool_result_msg["content"][0]["content"]
        assert len(tool_result_content) <= cap + len("\n[truncated]")
        assert tool_result_content.endswith("[truncated]")

    async def test_small_tool_result_is_not_truncated(self):
        """Tool results within cap must pass through unchanged."""
        from shared.react import react_loop

        client = MagicMock()
        client.messages.create.side_effect = [
            self._tool_response(),
            _end_turn_response("done"),
        ]

        small_output = "ticker: AAPL, price: 195"

        async def small_tool(_: dict) -> str:
            return small_output

        await react_loop(
            client=client,
            system="s",
            user_prompt="p",
            tools=[{"name": "my_tool", "description": "", "input_schema": {"type": "object", "properties": {}}}],
            executors={"my_tool": small_tool},
            model="claude-sonnet-4-6",
            max_tool_result_chars=8000,
        )

        second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
        tool_result_msg = second_call_messages[-1]
        tool_result_content = tool_result_msg["content"][0]["content"]
        assert tool_result_content == small_output
        assert "[truncated]" not in tool_result_content

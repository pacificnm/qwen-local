"""Agent loop, delta parsing, tool plumbing, and prompts (no network, no DB)."""

import asyncio

from app.llm import agent
from app.llm.client import DoneDelta, LLMClient, TextDelta, ThinkingDelta, ToolCallDelta
from app.llm.prompts import BASE_SYSTEM, append_context_summary, append_mode_instructions, build_system
from app.llm.tools import Tool, parse_tool_arguments

MODEL = "qwen3.8:27b-longctx"


class FakeClient:
    """Stands in for LLMClient: replays canned delta sequences per round."""

    def __init__(self, rounds: list):
        self.rounds = list(rounds)
        self.calls: list[dict] = []

    async def stream_chat(self, **kw):
        self.calls.append(kw)
        for delta in self.rounds.pop(0):
            yield delta


async def run(client, tools=(), max_rounds=3, cancel=None):
    events: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    result = await agent.run_turn(
        client=client,
        model=MODEL,
        system="sys",
        history=[{"role": "user", "content": "hi"}],
        tools=list(tools),
        emit=emit,
        cancel=cancel or asyncio.Event(),
        max_rounds=max_rounds,
    )
    return result, events


def echo_tool() -> Tool:
    async def handler(args: dict, _ctx) -> str:
        return f"echo:{args.get('q', '')}"

    return Tool(
        name="web_search",
        description="d",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=handler,
    )


def test_direct_answer_single_round():
    client = FakeClient([[TextDelta("He"), TextDelta("llo"), DoneDelta("stop")]])
    result, events = _sync(run(client))

    assert result.status == "done"
    assert result.text == "Hello"
    assert result.rounds == 1
    assert [name for name, _ in events] == ["token", "token", "done"]
    assert len(client.calls) == 1


def test_tool_round_then_answer():
    client = FakeClient(
        [
            [
                ToolCallDelta(0, id="c1", name="web_search", arguments_chunk='{"q": "'),
                ToolCallDelta(0, arguments_chunk='fastapi"}'),
                DoneDelta("tool_calls"),
            ],
            [TextDelta("Found it."), DoneDelta("stop")],
        ]
    )
    result, events = _sync(run(client, tools=[echo_tool()]))

    assert result.status == "done"
    assert result.text == "Found it."
    assert [name for name, _ in events] == [
        "tool_start",
        "tool_output",
        "tool_end",
        "token",
        "done",
    ]
    out = events[1][1]
    assert "echo:fastapi" in out["text"]
    assert result.tool_log[0].ok is True
    assert result.tool_log[0].arguments == {"q": "fastapi"}

    # The model's second call must carry the assistant tool_calls + tool result.
    second = client.calls[1]["messages"]
    assert second[-2]["role"] == "assistant"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "web_search"
    assert second[-1]["role"] == "tool"
    assert "echo:fastapi" in second[-1]["content"]


def test_thinking_streamed_but_not_answer():
    client = FakeClient(
        [[ThinkingDelta("let me"), ThinkingDelta(" think"), TextDelta("42"), DoneDelta("stop")]]
    )
    result, events = _sync(run(client))

    assert result.text == "42"
    assert [name for name, _ in events] == ["thinking", "thinking", "token", "done"]
    assert "".join(d["text"] for n, d in events if n == "thinking") == "let me think"


def test_client_parses_reasoning_delta():
    d = LLMClient._parse_delta('{"choices":[{"delta":{"reasoning":"hmm"}}]}')
    assert isinstance(d, ThinkingDelta) and d.text == "hmm"


def test_cancel_mid_stream_reports_cancelled():
    cancel = asyncio.Event()

    class MidCancelClient:
        async def stream_chat(self, **_kw):
            yield TextDelta("par")
            cancel.set()  # the endpoint's soft-stop, observed after this chunk
            yield TextDelta("tial")
            yield DoneDelta("stop")

    result, events = _sync(run(MidCancelClient(), cancel=cancel))

    assert result.status == "cancelled"
    assert result.text == "partial"
    assert events[-1] == ("cancelled", {"text": "partial"})


def test_cancel_before_first_round():
    client = FakeClient([[TextDelta("never")]])
    cancel = asyncio.Event()
    cancel.set()
    result, events = _sync(run(client, cancel=cancel))

    assert result.status == "cancelled"
    assert [name for name, _ in events] == ["cancelled"]
    assert client.calls == []  # no LLM call was even made


def test_forced_final_answer_after_tool_budget():
    tool_call = [
        ToolCallDelta(0, id="c1", name="web_search", arguments_chunk='{"q": "x"}'),
        DoneDelta("tool_calls"),
    ]
    client = FakeClient([list(tool_call), list(tool_call), [TextDelta("final"), DoneDelta("stop")]])
    result, events = _sync(run(client, tools=[echo_tool()], max_rounds=2))

    assert result.status == "done"
    assert "final" in result.text
    assert len(client.calls) == 3
    # Every budget round sent the tool schema; the forced call did not.
    assert len(client.calls[0]["tools"]) == 1
    assert len(client.calls[1]["tools"]) == 1
    assert client.calls[2]["tools"] is None


def test_unknown_tool_is_reported_not_fatal():
    client = FakeClient(
        [
            [ToolCallDelta(0, id="c1", name="nope", arguments_chunk="{}"), DoneDelta("tool_calls")],
            [TextDelta("ok"), DoneDelta("stop")],
        ]
    )
    result, events = _sync(run(client, tools=[echo_tool()]))

    assert result.status == "done"
    entry = result.tool_log[0]
    assert entry.ok is False
    assert "unknown tool" in entry.error


def test_llm_error_emits_error_event():
    class ErrClient:
        async def stream_chat(self, **_kw):
            raise agent.LLMError("model exploded")
            yield  # pragma: no cover  (makes this an async generator)

    client = ErrClient()
    result, events = _sync(run(client))

    assert result.status == "error"
    assert events[-1] == ("error", {"message": "model exploded"})


def test_tool_output_capped():
    big = "x" * (agent.TOOL_OUTPUT_CAP + 5)
    capped = agent._cap(big)
    assert len(capped) <= len(big)
    assert capped.endswith("KB]")


def test_parse_tool_arguments():
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments("not json")["_parse_error"] == "not json"
    assert parse_tool_arguments('"bare"') == {"value": "bare"}


def test_client_parse_delta_shapes():
    import json

    assert LLMClient._parse_delta('{"choices":[{"delta":{"content":"hi"}}]}') == TextDelta("hi")
    payload = json.dumps(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "a", "function": {"name": "f", "arguments": "{}"}}
                        ]
                    }
                }
            ]
        }
    )
    tc = LLMClient._parse_delta(payload)
    assert isinstance(tc, ToolCallDelta) and tc.name == "f"
    done = LLMClient._parse_delta('{"choices":[{"delta":{},"finish_reason":"stop"}]}')
    assert isinstance(done, DoneDelta) and done.finish_reason == "stop"
    assert LLMClient._parse_delta("not json") is None


def test_build_system_injects_chunks():
    system = build_system(
        "psf/requests",
        [
            {
                "file_path": "models.py",
                "start_line": 10,
                "end_line": 58,
                "language": "python",
                "content": "def send():\n    pass\n",
            }
        ],
    )
    assert "Codebase context: `psf/requests`" in system
    assert "### models.py [10-58] python" in system
    assert "def send()" in system
    assert system.startswith(BASE_SYSTEM)


def test_build_system_empty_is_base():
    assert build_system(None, []) == BASE_SYSTEM


def test_append_context_summary_noop_when_absent():
    assert append_context_summary(BASE_SYSTEM, None) == BASE_SYSTEM
    assert append_context_summary(BASE_SYSTEM, "") == BASE_SYSTEM


def test_append_context_summary_appends_section():
    out = append_context_summary(BASE_SYSTEM, "user asked to rename foo to bar")
    assert out.startswith(BASE_SYSTEM)
    assert "Summary of earlier conversation" in out
    assert "rename foo to bar" in out


def test_append_mode_instructions_code_is_noop():
    assert append_mode_instructions(BASE_SYSTEM, "code") == BASE_SYSTEM
    # Unrecognized values fail open to the full-behavior no-op — chat.py's
    # own validation is what actually rejects a bad mode before this runs.
    assert append_mode_instructions(BASE_SYSTEM, "bogus") == BASE_SYSTEM


def test_append_mode_instructions_ask_and_plan_append_nonempty_text():
    for mode in ("ask", "plan"):
        out = append_mode_instructions(BASE_SYSTEM, mode)
        assert out.startswith(BASE_SYSTEM)
        assert len(out) > len(BASE_SYSTEM)
        # Explicitly overrides BASE_SYSTEM's blanket code_interpreter mention.
        assert "code_interpreter" in out
        assert "shell" in out
    ask = append_mode_instructions(BASE_SYSTEM, "ask")
    plan = append_mode_instructions(BASE_SYSTEM, "plan")
    assert ask != plan
    assert "Mode: Ask" in ask
    assert "Mode: Plan" in plan


def _sync(coro):
    return asyncio.new_event_loop().run_until_complete(coro)

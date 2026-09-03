"""Qwen-Agent adapter tests (no LLM, no network).

`run_turn` is exercised through its `assistant` seam: fake assistants yield
accumulated `Message` lists exactly like Qwen-Agent does, so the SSE
translation, terminal-event and cancellation logic are covered offline.
The `BaseTool` wrappers are driven on a worker thread via `asyncio.to_thread`
— the same topology as production (the loop thread owns the event loop, the
Qwen-Agent thread owns `call()`).

Coverage:
- run_turn: token/thinking deltas, exactly one terminal event, error path,
  user-Stop during an in-flight (simulated slow) LLM step.
- LLM wiring: `build_llm` raw-API config (native `tools`, generate_cfg
  placement) and the `reasoning` → `reasoning_content` Ollama shim, driven
  through the REAL `raw_chat` → `_chat_stream` adapter code with a fake
  Ollama endpoint (no network).
- TurnRuntime: wait_async normal + cancel (CancelledError escapes, future
  cancelled), emit_event from the worker thread.
- Tools: SSE contract (tool_start/tool_output/tool_end), MONOTONIC per-turn
  index, repo write/read/edit behavior, repo_commit delegation.
"""

import asyncio
import subprocess
import time

import pytest

import app.agents.qwen_assistant as qa
import app.agents.tools as t
import app.core.settings as cs
import app.repos.gitops as gitops

# --- helpers ----------------------------------------------------------------


class FakeAssistant:
    """Yields the given accumulated `Message` lists, like Assistant.run."""

    def __init__(self, yields: list[list[dict]], error: Exception | None = None):
        self.yields = yields
        self.error = error
        self.received_messages: list[dict] | None = None

    def run(self, messages, **kwargs):
        self.received_messages = messages
        if self.error is not None:
            raise self.error
        yield from self.yields


async def _collect(run_turn_kwargs: dict) -> tuple[str, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    cancel = asyncio.Event()
    kwargs = dict(
        model="fake-model",
        system="SYS",
        history=[{"role": "user", "content": "hi"}],
        emit=emit,
        cancel=cancel,
    )
    kwargs.update(run_turn_kwargs)
    status = await qa.run_turn(**kwargs)
    return status, events


def _git_init_repo(path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.co"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True)


async def _run_tool(tool, args_dict: dict) -> str:
    """Invoke a BaseTool exactly as the Qwen-Agent loop would (worker thread)."""
    return await asyncio.to_thread(tool.call, args_dict)


# --- run_turn: SSE contract ---------------------------------------------------


async def test_run_turn_translates_streaming_deltas():
    fake = FakeAssistant(yields=[
        [{"role": "assistant", "content": "", "reasoning_content": "hmm"}],
        [{"role": "assistant", "content": "He", "reasoning_content": "hmm, let me think"}],
        [{"role": "assistant", "content": "Hello", "reasoning_content": "hmm, let me think"}],
    ])
    status, events = await _collect({"assistant": fake})
    assert status == "done"
    kinds = [n for n, _ in events]
    assert kinds == ["thinking", "thinking", "token", "token", "done"]
    assert events[0] == ("thinking", {"text": "hmm"})
    assert events[1][1]["text"] == ", let me think"
    assert events[2] == ("token", {"text": "He"})
    assert events[3] == ("token", {"text": "llo"})
    assert fake.received_messages[0] == {"role": "system", "content": "SYS"}


async def test_run_turn_emits_error_on_tool_or_llm_failure():
    fake = FakeAssistant(yields=[], error=RuntimeError("kaboom"))
    status, events = await _collect({"assistant": fake})
    assert status == "error"
    assert events[-1][0] == "error"
    assert "kaboom" in events[-1][1]["message"]
    assert len([n for n, _ in events if n in ("done", "cancelled", "error")]) == 1


async def test_run_turn_cancels_when_stop_fires_before_yield():
    class SlowAssistant:
        def run(self, messages, **kwargs):
            time.sleep(2.0)  # simulates an in-flight LLM call on the worker thread
            yield [{"role": "assistant", "content": "too late"}]

    events: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.call_later(0.3, cancel.set)
    status = await qa.run_turn(
        model="m", system="s", history=[], emit=emit, cancel=cancel,
        assistant=SlowAssistant(),
    )
    assert status == "cancelled"
    assert events[-1][0] == "cancelled"


async def test_run_turn_skips_function_results():
    # Real FnCallAgent yield progression: [a1] → [a1, f1] → [a1, f1, a2]
    # → final [a1, f1, a2]. Never the input history; FUNCTION content must
    # not be streamed to the UI as tokens.
    from qwen_agent.llm.schema import FUNCTION

    a1 = {"role": "assistant", "content": ""}
    f1 = {"role": FUNCTION, "name": "web_search",
          "content": "tool result — must NOT be streamed as tokens"}
    a2 = {"role": "assistant", "content": "ok"}
    fake = FakeAssistant(yields=[
        [a1],
        [a1, f1],
        [a1, f1, a2],
        [a1, f1, a2],  # final `yield response` after the loop
    ])
    _, events = await _collect({"assistant": fake})
    tokens = [d["text"] for n, d in events if n == "token"]
    assert tokens == ["ok"]
    assert not any("tool result" in d.get("text", "") for n, d in events)


async def test_run_turn_final_answer_after_tool_budget(monkeypatch):
    """The model spent its whole per-run LLM-call budget on tool calls:
    the turn's last cumulative list ends on a FUNCTION (tool result) with
    NO assistant answer. The adapter must still end with an answer, via
    one final tool-less LLM call."""
    from qwen_agent.llm.schema import FUNCTION

    a1 = {"role": "assistant", "content": "Let me read more files."}
    f1 = {"role": FUNCTION, "name": "repo_read_file", "content": "file body …"}
    seen_history: list = []

    def fake_final(assistant, history):
        seen_history.append(list(history))
        return iter([[{"role": "assistant", "content": "Here is my review of the files."}]])

    monkeypatch.setattr(qa, "final_answer_stream", fake_final)
    fake = FakeAssistant(yields=[
        [a1],
        [a1, f1],   # final cumulative list: ends on the tool result, no answer
        [a1, f1],
    ])
    status, events = await _collect({"assistant": fake})
    assert status == "done"
    seen = [m for m in seen_history if m][-1]
    # The fallback call must be re-anchored onto the input messages —
    # FnCallAgent's yields are only the accumulated suffix, and
    # Qwen-Agent's validator 400s a request that starts on an
    # assistant/tool message ("must start with a user message").
    assert seen[0] == {"role": "system", "content": "SYS"}
    assert seen[1] == {"role": "user", "content": "hi"}
    assert seen[-1]["role"] == FUNCTION
    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert "Here is my review of the files." in tokens
    assert "file body …" not in tokens  # tool results stay out of the answer
    assert len([n for n, _ in events if n in ("done", "cancelled", "error")]) == 1


async def test_run_turn_final_answer_after_thinking_only_tail(monkeypatch):
    """The live 4B shape: after the last tool result the model's final call
    is thinking-only — the loop ends on an assistant message with EMPTY
    content (no tokens were streamed). The adapter must force a final
    answer from the same history."""
    from qwen_agent.llm.schema import FUNCTION

    a1 = {"role": "assistant", "content": "", "reasoning_content": "t1"}
    f1 = {"role": FUNCTION, "name": "repo_read_file", "content": "file body …"}
    a2 = {"role": "assistant", "content": "", "reasoning_content": "t1, more thinking"}
    fake = FakeAssistant(yields=[
        [a1],
        [a1, f1],
        [a1, f1, a2],   # final call is thinking-only: empty content ends the loop
        [a1, f1, a2],
    ])
    monkeypatch.setattr(
        qa, "final_answer_stream",
        lambda assistant, history: iter([[{"role": "assistant", "content": "Review complete."}]])
    )
    status, events = await _collect({"assistant": fake})
    assert status == "done"
    assert "".join(d["text"] for n, d in events if n == "token") == "Review complete."
    # thinking suffixes are tracked per message slot — slot 2 (a2) was new,
    # so its full reasoning text is a single event.
    assert [d["text"] for n, d in events if n == "thinking"] == ["t1", "t1, more thinking"]


def test_needs_final_answer_shapes():
    from qwen_agent.llm.schema import FUNCTION

    fn = {"role": FUNCTION, "name": "t", "content": "r"}
    empty = {"role": "assistant", "content": ""}
    blank = {"role": "assistant", "content": "   "}
    ok = {"role": "assistant", "content": "review done"}
    assert qa._needs_final_answer([{"role": "assistant", "content": "preamble"}, fn])
    assert qa._needs_final_answer([ok, fn])
    assert qa._needs_final_answer([empty])
    assert qa._needs_final_answer([ok, empty])
    assert qa._needs_final_answer([blank])
    assert not qa._needs_final_answer([fn, ok])
    assert not qa._needs_final_answer([ok])


def test_final_answer_stream_strips_dangling_empty_assistant():
    """A thinking-only tail leaves a dangling empty assistant message; it
    carries no content and must not be the last request message of the
    fallback call."""
    from qwen_agent.llm.schema import FUNCTION

    class _A:
        def __init__(self):
            self.calls: list = []

        def _call_llm(self, messages, functions, stream, extra_generate_cfg):
            self.calls.append((list(messages), functions))
            return iter([[{"role": "assistant", "content": "done"}]])

    a = _A()
    history = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "review the readme"},
        {"role": "assistant", "content": ""},
        {"role": FUNCTION, "name": "repo_read_file", "content": "body"},
        {"role": "assistant", "content": "   ", "reasoning_content": "think"},
    ]
    out = list(qa.final_answer_stream(a, history))
    msgs, functions = a.calls[0]
    assert functions is None
    assert msgs[-1] == history[3]  # ends on the tool result, tail stripped
    assert out == [[{"role": "assistant", "content": "done"}]]


async def test_run_turn_no_final_answer_when_turn_ends_naturally(monkeypatch):
    """When the turn legitimately ends with an assistant answer, the
    budget-exhaustion fallback must NOT add another LLM call."""

    def boom(*a, **kw):
        raise AssertionError("final_answer_stream must not be called")

    monkeypatch.setattr(qa, "final_answer_stream", boom)
    a1 = {"role": "assistant", "content": "ok"}
    fake = FakeAssistant(yields=[[a1], [a1]])
    status, events = await _collect({"assistant": fake})
    assert status == "done"
    assert [d["text"] for n, d in events if n == "token"] == ["ok"]


# --- TurnRuntime bridge --------------------------------------------------------


async def test_wait_async_returns_result():
    loop = asyncio.get_running_loop()
    rt = t.TurnRuntime(emit=None, cancel=asyncio.Event(), loop=loop)

    async def work() -> int:
        return 42

    assert await asyncio.to_thread(rt.wait_async, work()) == 42


async def test_wait_async_cancel_raises_cancelled_error():
    loop = asyncio.get_running_loop()
    cancel = asyncio.Event()
    rt = t.TurnRuntime(emit=None, cancel=cancel, loop=loop)
    cancel.set()

    async def slow() -> None:
        await asyncio.sleep(10)

    with pytest.raises(asyncio.CancelledError):
        rt.wait_async(slow())
    assert rt.aborted is True


async def test_emit_event_from_worker_thread():
    loop = asyncio.get_running_loop()
    seen: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        seen.append((name, data))

    rt = t.TurnRuntime(emit=emit, cancel=asyncio.Event(), loop=loop)
    await asyncio.to_thread(rt.emit_event, "token", {"text": "x"})
    assert seen == [("token", {"text": "x"})]


# --- BaseTool wrappers: SSE contract -------------------------------------------


def _runtime(tmp_path, monkeypatch, full_repo: bool = True) -> tuple[t.TurnRuntime, list]:
    """A runtime with an in-memory event collector + a workspace under tmp_path."""
    settings = cs.Settings(workspace_dir=str(tmp_path))
    monkeypatch.setattr(t, "get_settings", lambda: settings)
    if full_repo:
        (tmp_path / "o__r" / ".git").mkdir(parents=True)
    events: list[tuple[str, dict]] = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    loop = asyncio.get_running_loop()
    return t.TurnRuntime(emit=emit, cancel=asyncio.Event(), loop=loop), events


async def test_repo_write_read_edit_tools_emit_sse_contract(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch)
    repo_dir = tmp_path / "o__r"
    (repo_dir / "existing.txt").write_text("alpha\nbeta\ngamma\n")

    w = t.RepoWriteFile(rt, "o/r")
    e = t.RepoEditFile(rt, "o/r")
    r = t.RepoReadFile(rt, "o/r")

    out1 = await _run_tool(w, {"path": "notes/a.md", "content": "hi\n"})
    assert (repo_dir / "notes" / "a.md").read_text() == "hi\n"
    assert "notes/a.md" in out1

    out2 = await _run_tool(e, {"path": "existing.txt", "old_string": "beta", "new_string": "BETA"})
    assert "existing.txt" in out2 and "1 occurrence" in out2
    assert (repo_dir / "existing.txt").read_text() == "alpha\nBETA\ngamma\n"

    out3 = await _run_tool(r, {"path": "existing.txt"})
    assert "alpha\nBETA\ngamma\n" in out3 and "existing.txt" in out3

    # Monotonic per-turn index across different tools.
    starts = [d["index"] for n, d in events if n == "tool_start"]
    assert starts == [0, 1, 2]
    ends = [d for n, d in events if n == "tool_end"]
    assert [d["index"] for d in ends] == [0, 1, 2]
    assert all(d["ok"] for d in ends)
    outputs = [d for n, d in events if n == "tool_output"]
    assert outputs[0]["index"] == 0 and isinstance(outputs[0]["text"], str)


async def test_repo_edit_file_requires_unique_old_string(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch)
    repo_dir = tmp_path / "o__r"
    (repo_dir / "dup.txt").write_text("x\nx\n")

    e = t.RepoEditFile(rt, "o/r")
    out = await _run_tool(e, {"path": "dup.txt", "old_string": "x", "new_string": "y"})
    assert "occurs 2 times" in out
    assert (repo_dir / "dup.txt").read_text() == "x\nx\n"  # untouched

    # replace_all works.
    out = await _run_tool(e, {"path": "dup.txt", "old_string": "x", "new_string": "y", "replace_all": True})
    assert "2 occurrence" in out
    assert (repo_dir / "dup.txt").read_text() == "y\ny\n"


async def test_repo_tools_reject_traversal_and_missing_paths(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch)
    w = t.RepoWriteFile(rt, "o/r")
    r = t.RepoReadFile(rt, "o/r")

    out = await _run_tool(w, {"path": "../escape.txt", "content": "x"})
    assert "invalid path" in out
    assert not (tmp_path / "escape.txt").exists()

    out = await _run_tool(r, {"path": "no/such/file"})
    assert "file not found" in out


async def test_repo_list_files_filters_by_prefix(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch)
    repo_dir = tmp_path / "o__r"
    _git_init_repo(repo_dir)
    (repo_dir / "README.md").write_text("root\n")
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("print(1)\n")
    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "c"], check=True, capture_output=True)

    ll = t.RepoListFiles(rt, "o/r")
    out = await _run_tool(ll, {})
    assert "README.md" in out and "src/app.py" in out

    out = await _run_tool(ll, {"path": "src"})
    assert "src/app.py" in out and "README.md" not in out


async def test_repo_commit_delegates_to_gitops(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch)

    calls: dict = {}

    async def fake_commit_workspace(ws, full_name, pat, **kw):
        calls.update(ws=str(ws), full_name=full_name, pat=pat, **kw)
        return gitops.CommitResult(branch="b-1", commit_sha="abc123", pr_url="https://pr/1")

    monkeypatch.setattr(t.gitops, "commit_workspace", fake_commit_workspace)

    c = t.RepoCommit(rt, "o/r")
    out = await _run_tool(c, {"message": "m", "open_pr": True, "pr_title": "T", "pr_body": "B"})
    assert "b-1" in out and "abc123" in out and "https://pr/1" in out
    assert calls["full_name"] == "o/r"
    assert calls["open_pr"] is True and calls["pr_title"] == "T"
    assert calls["message"] == "m"


async def test_repo_commit_maps_giterrors_to_model_facing_text(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch)

    async def boom(*a, **kw):
        raise gitops.GitError("nothing to commit")

    monkeypatch.setattr(t.gitops, "commit_workspace", boom)
    c = t.RepoCommit(rt, "o/r")
    out = await _run_tool(c, {"message": "m"})
    assert "commit failed" in out and "nothing to commit" in out
    # The tool call itself completed (no exception): model gets the message.
    assert events[-1][0] == "tool_end" and events[-1][1]["ok"] is True


async def test_general_tools_registered_unconditionally(tmp_path, monkeypatch):
    rt, events = _runtime(tmp_path, monkeypatch, full_repo=False)
    tools = t.build_tools(rt, None)
    assert [x.name for x in tools] == ["web_search", "code_interpreter", "shell"]
    class R:
        github_full_name = "o/r"
    tools = t.build_tools(rt, R())
    # + docker_stop/docker_logs/docker_exec (project sandbox, repo-scoped) + 5 repo tools + 5 check tools
    assert len(tools) == 16 and tools[3].name == "docker_stop"
    assert tools[6].name == "repo_list_files"


READONLY_TOOL_NAMES = {
    "web_search", "docker_logs", "repo_list_files", "repo_read_file",
    "frontend_lint", "frontend_typecheck", "backend_lint",
    "backend_typecheck", "backend_tests",
}


async def test_build_tools_ask_and_plan_modes_restrict_to_readonly(tmp_path, monkeypatch):
    rt, _events = _runtime(tmp_path, monkeypatch, full_repo=False)
    class R:
        github_full_name = "o/r"
    for mode in ("ask", "plan"):
        # No repo: only web_search survives (code_interpreter/shell are mutating).
        tools = t.build_tools(rt, None, mode=mode)
        assert {x.name for x in tools} == {"web_search"}
        assert all(x.readonly for x in tools)

        # Repo-bound: the full readonly subset, nothing mutating slips through.
        tools = t.build_tools(rt, R(), mode=mode)
        assert {x.name for x in tools} == READONLY_TOOL_NAMES
        assert all(x.readonly for x in tools)


async def test_build_tools_code_mode_default_and_explicit_are_unchanged(tmp_path, monkeypatch):
    rt, _events = _runtime(tmp_path, monkeypatch, full_repo=False)
    class R:
        github_full_name = "o/r"
    assert [x.name for x in t.build_tools(rt, None)] == ["web_search", "code_interpreter", "shell"]
    assert [x.name for x in t.build_tools(rt, None, mode="code")] == [
        "web_search", "code_interpreter", "shell",
    ]
    assert len(t.build_tools(rt, R())) == 16
    assert len(t.build_tools(rt, R(), mode="code")) == 16


# --- LLM wiring: raw API + Ollama `reasoning` shim ---------------------------


class _FakeSettings:
    effective_ollama_host = "http://localhost:11434"


def test_build_llm_raw_api_and_generate_cfg_placement(monkeypatch):
    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())
    llm = qa.build_llm("qwen3.8:27b-longctx")
    assert isinstance(llm, qa.OllamaTextChat)

    # Raw (native `tools`) API — Qwen-Agent's default Nous prompt-injection
    # path fails against Ollama (HTTP 500 EOF).
    assert llm.use_raw_api is True
    assert llm.model == "qwen3.8:27b-longctx"
    assert llm.max_retries == 2

    # Generate params must live in the `generate_cfg` sub-dict — the base
    # model reads that dict; top-level cfg keys are silently ignored.
    gc = llm.generate_cfg
    assert gc["temperature"] == qa.DEFAULT_TEMPERATURE
    assert gc["max_tokens"] == qa.DEFAULT_MAX_TOKENS
    assert gc["max_input_tokens"] == qa.DEFAULT_MAX_INPUT_TOKENS
    assert gc["request_timeout"] == qa.DEFAULT_REQUEST_TIMEOUT
    assert "use_raw_api" not in gc  # consumed by BaseChatModel.__init__
    assert "max_retries" not in gc


async def test_assistant_keeps_passed_llm_instance(monkeypatch):
    """Passing an LLM *instance* (not a cfg dict) must keep that exact
    object — a cfg would be re-instantiated, silently losing
    `use_raw_api` and the Ollama `reasoning` shim."""
    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())
    llm = qa.build_llm("qwen3.8:27b-longctx")
    assistant = qa.Assistant(function_list=[], llm=llm, name="t")
    assert assistant.llm is llm
    assert assistant.llm.use_raw_api is True


async def test_build_assistant_wires_raw_llm_and_tools(monkeypatch):
    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())
    loop = asyncio.get_running_loop()
    rt = t.TurnRuntime(
        emit=lambda *_: None, cancel=asyncio.Event(), loop=loop  # type: ignore[arg-type]
    )
    assistant = qa.build_assistant("qwen3.8:27b-longctx", t.build_tools(rt, None))
    assert type(assistant.llm) is qa.OllamaTextChat
    assert assistant.llm.use_raw_api is True
    assert set(assistant.function_map) == {"web_search", "code_interpreter", "shell"}


# --- effort levels (UI selector → request-body params) ----------------------


def test_effort_levels_shape_and_default():
    # Exactly the four UI levels, in the canonical order.
    assert set(qa.EFFORT_LEVELS) == {"low", "medium", "high", "xhigh"}
    assert qa.DEFAULT_EFFORT == "medium"
    # Every body carries the three per-request knobs.
    for body in qa.EFFORT_LEVELS.values():
        assert set(body) == {"think", "reasoning_effort", "max_thinking_tokens"}
        assert body["reasoning_effort"] in ("low", "medium", "high")
    # The user's exact mapping.
    expected = {
        "low": (False, "low", 0),
        "medium": (True, "low", 2048),
        "high": (True, "medium", 8192),
        "xhigh": (True, "high", 16384),
    }
    for level, (think, reasoning_effort, max_thinking_tokens) in expected.items():
        assert qa.EFFORT_LEVELS[level] == {
            "think": think,
            "reasoning_effort": reasoning_effort,
            "max_thinking_tokens": max_thinking_tokens,
        }
    # Higher levels think harder.
    assert qa.EFFORT_LEVELS["low"]["max_thinking_tokens"] < qa.EFFORT_LEVELS["medium"]["max_thinking_tokens"]
    assert qa.EFFORT_LEVELS["xhigh"]["reasoning_effort"] == "high"


def test_effort_body_falls_back_to_default():
    assert qa.effort_body(None) == qa.EFFORT_LEVELS[qa.DEFAULT_EFFORT]
    assert qa.effort_body("") == qa.EFFORT_LEVELS[qa.DEFAULT_EFFORT]
    assert qa.effort_body("bogus") == qa.EFFORT_LEVELS[qa.DEFAULT_EFFORT]
    assert qa.effort_body("xhigh") == qa.EFFORT_LEVELS["xhigh"]


def _assert_extra_body(eb: dict, level: str) -> None:
    for key, value in qa.EFFORT_LEVELS[level].items():
        assert eb[key] == value
    # Usage block for the context-used UI, requested alongside the effort knobs.
    assert eb["stream_options"] == {"include_usage": True}


def test_build_llm_embeds_effort_extra_body(monkeypatch):
    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())
    for level in qa.EFFORT_LEVELS:
        _assert_extra_body(qa.build_llm("fake-model", level).generate_cfg["extra_body"], level)
    # Absent → DEFAULT_EFFORT.
    _assert_extra_body(qa.build_llm("fake-model").generate_cfg["extra_body"], qa.DEFAULT_EFFORT)
    # Unknown label → DEFAULT_EFFORT (never KeyError).
    _assert_extra_body(qa.build_llm("fake-model", "nonsense").generate_cfg["extra_body"], qa.DEFAULT_EFFORT)


def test_build_assistant_threads_effort(monkeypatch):
    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())
    assistant = qa.build_assistant("fake-model", [], "xhigh")
    assert type(assistant.llm) is qa.OllamaTextChat
    _assert_extra_body(assistant.llm.generate_cfg["extra_body"], "xhigh")


async def test_run_turn_threads_effort_to_built_assistant(monkeypatch):
    """`run_turn(effort=…)` must reach the Assistant it builds.

    (A caller-supplied `assistant` seam owns its own LLM and ignores
    `effort` — the endpoint always builds fresh, so the default path is
    what gets exercised here.)"""
    captured: list = []

    class _CapAssistant:
        def run(self, messages, **kwargs):
            yield [{"role": "assistant", "content": "ok"}]

    def fake_build_assistant(model, tools, effort=None):
        captured.append(effort)
        return _CapAssistant()

    monkeypatch.setattr(qa, "build_assistant", fake_build_assistant)

    status, _ = await _collect({"effort": "xhigh"})
    assert status == "done"
    assert captured == ["xhigh"]

    captured.clear()
    await _collect({})
    assert captured == [qa.DEFAULT_EFFORT]  # absent → medium


def _make_ollama_chunk(delta: dict, finish: str | None = None):
    from openai.types.chat import ChatCompletionChunk

    return ChatCompletionChunk.model_validate(
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def test_raw_chat_shim_surfaces_reasoning_and_tool_calls(monkeypatch):
    """Drive the REAL `raw_chat` → `_chat_stream` (oai.py) adapter against a
    fake Ollama endpoint that emits exactly what the live repro showed:
    thinking in `delta.reasoning`, text in `delta.content`, a complete call
    in `delta.tool_calls`. Asserts thinking and the tool call both survive
    into Qwen-Agent `Message`s (previously: thinking dropped, tool calls
    never parsed)."""
    import qwen_agent.llm.oai as oai_mod
    from qwen_agent.llm.schema import SYSTEM, USER, Message

    seen: dict = {}
    # Real Ollama semantics: per-chunk INCREMENTAL deltas (the adapter
    # accumulates them), never cumulative repeats.
    chunks = [
        _make_ollama_chunk({"content": "", "reasoning": "step one"}),
        _make_ollama_chunk({"content": "I will", "reasoning": ", now writing"}),
        _make_ollama_chunk(
            {
                "content": "",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "repo_write_file",
                            "arguments": '{"path": "notes/a.md", "content": "hi"}',
                        },
                    }
                ],
            },
            finish="tool_calls",
        ),
    ]

    class _FakeCompletions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return iter(chunks)

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **init_kwargs):
            seen.setdefault("client_init", {}).update(init_kwargs)
            self.chat = _FakeChat()

    monkeypatch.setattr(oai_mod.openai, "OpenAI", _FakeOpenAI)

    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())
    llm = qa.build_llm("fake-model")

    functions = [
        {
            "type": "function",
            "function": {
                "name": "repo_write_file",
                "description": "write a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            },
        }
    ]
    messages = [
        Message(role=SYSTEM, content="SYS"),
        Message(role=USER, content="write the note"),
    ]

    # `raw_chat` is exactly what `BaseChatModel.chat()` calls when
    # `use_raw_api` is set (stream=True, delta_stream=False).
    out = list(
        llm.raw_chat(
            messages=messages,
            functions=functions,
            stream=True,
            generate_cfg={"temperature": 0.3, "request_timeout": 300},
        )
    )

    # Request seen by the (fake) Ollama endpoint.
    assert seen["stream"] is True
    assert seen["model"] == "fake-model"
    assert seen["tools"] == functions  # native tools, NOT prompt-injected
    assert seen["timeout"] == 300  # request_timeout mapped by the oai wrapper
    assert seen["client_init"].get("base_url") == "http://localhost:11434/v1"

    # Final (cumulative) batch carries thinking, text and the tool call.
    final = [m.model_dump() for m in out[-1]]
    by_role = [m for m in final]
    thinking = [m for m in by_role if m.get("reasoning_content") and not m.get("function_call")]
    assert thinking and thinking[0]["reasoning_content"] == "step one, now writing"
    textual = [m for m in by_role if m.get("content") and not m.get("function_call")]
    assert textual and textual[0]["content"] == "I will"
    calls = [m for m in by_role if m.get("function_call")]
    assert len(calls) == 1
    assert calls[0]["function_call"]["name"] == "repo_write_file"
    assert "notes/a.md" in calls[0]["function_call"]["arguments"]
    assert calls[0]["extra"]["function_id"] == "call_1"


def test_effort_extra_body_reaches_endpoint(monkeypatch):
    """The effort knobs must survive the REAL production path —
    `build_llm` → `raw_chat` → `_chat_stream` → the openai SDK `create` call —
    as a top-level request param. `extra_body` is the only channel by which
    a param the OpenAI SDK doesn't know about can reach Ollama's body (a bare
    kwarg would be rejected); this asserts it arrives un-mangled for xhigh."""
    import qwen_agent.llm.oai as oai_mod
    from qwen_agent.llm.schema import USER, Message

    seen: dict = {}
    chunks = [
        _make_ollama_chunk({"content": "ok", "reasoning": "hmm"}, finish="stop"),
    ]

    class _FakeCompletions:
        def create(self, **kwargs):
            seen.update(kwargs)
            return iter(chunks)

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **init_kwargs):
            seen.setdefault("client_init", {}).update(init_kwargs)
            self.chat = _FakeChat()

    monkeypatch.setattr(oai_mod.openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(qa, "get_settings", lambda: _FakeSettings())

    llm = qa.build_llm("fake-model", "xhigh")
    # Drive with the model's OWN generate_cfg (the production path), not an
    # ad-hoc dict — that's where `extra_body` lives.
    _ = list(
        llm.raw_chat(
            messages=[Message(role=USER, content="hi")],
            stream=True,
            generate_cfg=llm.generate_cfg,
        )
    )

    _assert_extra_body(seen["extra_body"], "xhigh")


def _usage_chunk(usage: dict):
    """The stream's final chunk: empty `choices`, a `usage` block — exactly
    what Ollama appends for `stream_options.include_usage`."""
    from openai.types.chat import ChatCompletionChunk

    payload = {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [],
    }
    if usage is not None:
        payload["usage"] = usage
    return ChatCompletionChunk.model_validate(payload)


def test_stream_usage_captured_into_last_usage(monkeypatch):
    """`OllamaTextChat` latches the usage block of the stream's final chunk
    into `last_usage` — the `prompt_tokens` figure behind the UI's context-
    used display. Chunks without usage (the ones carrying content) must not
    clobber a captured value; a stream with no usage block leaves it None."""
    import qwen_agent.llm.oai as oai_mod
    from qwen_agent.llm.schema import USER, Message

    def build(chunks: list):
        class _FakeCompletions:
            def create(self, **kwargs):
                return iter(chunks)

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeOpenAI:
            def __init__(self, **init_kwargs):
                self.chat = _FakeChat()

        monkeypatch.setattr(oai_mod.openai, "OpenAI", _FakeOpenAI)
        llm = qa.build_llm("fake-model")
        messages = [Message(role=USER, content="hi")]
        _ = list(llm.raw_chat(messages=messages, stream=True, generate_cfg=llm.generate_cfg))
        return llm

    # Content chunks, then the usage-bearing final chunk (live Ollama shape).
    llm = build([
        _make_ollama_chunk({"content": "ok"}),
        _usage_chunk({"prompt_tokens": 12, "completion_tokens": 16, "total_tokens": 28}),
    ])
    assert llm.last_usage == {"prompt_tokens": 12, "completion_tokens": 16, "total_tokens": 28}

    # No usage block anywhere → last_usage stays unset.
    llm = build([_make_ollama_chunk({"content": "ok"}, finish="stop")])
    assert llm.last_usage is None

    # A usage-less chunk AFTER a captured one must not erase it (Ollama only
    # sends the block once, in the final chunk).
    llm = build([
        _usage_chunk({"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}),
        _make_ollama_chunk({"content": "late"}),
    ])
    assert llm.last_usage == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}


def test_assistant_usage_reads_llm_last_usage():
    import types

    class _NoLlm:
        pass

    assert qa._assistant_usage(_NoLlm()) is None
    assert qa._assistant_usage(types.SimpleNamespace(llm=types.SimpleNamespace(last_usage=None))) is None
    good = {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49}
    fake = types.SimpleNamespace(llm=types.SimpleNamespace(last_usage=good))
    assert qa._assistant_usage(fake) == good
    # A non-dict last_usage is treated as missing, not trusted.
    bad = types.SimpleNamespace(llm=types.SimpleNamespace(last_usage="nope"))
    assert qa._assistant_usage(bad) is None


async def test_done_event_carries_usage():
    """`done` carries `usage` when the LLM captured one (the frontend's
    `contextUsed` source); without a capture the event stays `{"text": …}`."""
    import types

    class _UsageAssistant:
        llm = types.SimpleNamespace(
            last_usage={"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49}
        )

        def run(self, messages, **kwargs):
            yield [{"role": "assistant", "content": "ok"}]

    status, events = await _collect({"assistant": _UsageAssistant()})
    assert status == "done"
    done = [d for n, d in events if n == "done"]
    assert done and done[0]["usage"] == {
        "prompt_tokens": 42,
        "completion_tokens": 7,
        "total_tokens": 49,
    }
    assert done[0]["text"] == "ok"

    # FakeAssistant has no `llm` attr → `_assistant_usage` → None.
    plain = FakeAssistant(yields=[[{"role": "assistant", "content": "ok"}]])
    status, events = await _collect({"assistant": plain})
    assert status == "done"
    done = [d for n, d in events if n == "done"]
    assert done and "usage" not in done[0]


def test_parse_num_ctx():
    from app.api.models_api import _parse_num_ctx

    assert _parse_num_ctx("num_ctx 128000\nnum_predict 8192\nnum_gpu 0") == 128000
    assert _parse_num_ctx("mirror 1\nstop ") is None
    assert _parse_num_ctx("") is None

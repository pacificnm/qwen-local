"""Tool-call bookkeeping tests for the chat SSE pipeline (`_apply_tool_event`).

Regression coverage for a real gap: `Message.tool_calls` never accumulated
`tool_output` text at all — only `name`/`arguments`/`ok`/`duration_ms` were
persisted, so any historical view of a tool call (e.g. the Tool Calls tab's
click-to-inspect modal) showed "(no output)" for every past call, even
though the tool clearly produced output live during the turn.
"""

from app.api.chat import TOOL_OUTPUT_PERSIST_CAP, _apply_tool_event


def test_tool_start_appends_entry_with_index():
    tools: list[dict] = []
    _apply_tool_event(tools, "tool_start", {"tool": "repo_read_file", "index": 0, "arguments": {"path": "a.py"}})
    assert tools == [{"name": "repo_read_file", "arguments": {"path": "a.py"}, "_index": 0}]


def test_tool_output_sets_output_for_non_streaming_tool():
    tools = [{"name": "repo_list_files", "arguments": {}, "_index": 0}]
    _apply_tool_event(tools, "tool_output", {"index": 0, "text": "a.py\nb.py\n"})
    assert tools[0]["output"] == "a.py\nb.py\n"


def test_tool_output_accumulates_multiple_chunks_for_streaming_tool():
    tools = [{"name": "shell", "arguments": {"command": "echo hi"}, "_index": 0}]
    for chunk in ("hi", "\n", "done"):
        _apply_tool_event(tools, "tool_output", {"index": 0, "text": chunk})
    assert tools[0]["output"] == "hi\ndone"


def test_tool_output_routes_by_index_not_array_position():
    """Two tool calls in one turn: output for call 1 must not leak onto call 0."""
    tools = [
        {"name": "repo_read_file", "arguments": {"path": "a.py"}, "_index": 0},
        {"name": "repo_read_file", "arguments": {"path": "b.py"}, "_index": 1},
    ]
    _apply_tool_event(tools, "tool_output", {"index": 1, "text": "content of b"})
    _apply_tool_event(tools, "tool_output", {"index": 0, "text": "content of a"})
    assert tools[0]["output"] == "content of a"
    assert tools[1]["output"] == "content of b"


def test_tool_end_sets_ok_and_duration_on_matching_entry():
    tools = [
        {"name": "repo_read_file", "arguments": {}, "_index": 0},
        {"name": "repo_write_file", "arguments": {}, "_index": 1},
    ]
    _apply_tool_event(tools, "tool_end", {"index": 1, "ok": False, "duration_ms": 42})
    assert tools[0].get("ok") is None
    assert tools[1]["ok"] is False
    assert tools[1]["duration_ms"] == 42


def test_tool_output_truncates_past_persist_cap():
    tools = [{"name": "shell", "arguments": {}, "_index": 0}]
    _apply_tool_event(tools, "tool_output", {"index": 0, "text": "x" * (TOOL_OUTPUT_PERSIST_CAP + 500)})
    out = tools[0]["output"]
    assert len(out) <= TOOL_OUTPUT_PERSIST_CAP
    assert out.endswith("KB]")
    assert tools[0]["_capped"] is True


def test_tool_output_stops_accumulating_once_capped():
    tools = [{"name": "shell", "arguments": {}, "_index": 0}]
    _apply_tool_event(tools, "tool_output", {"index": 0, "text": "x" * (TOOL_OUTPUT_PERSIST_CAP + 500)})
    capped_output = tools[0]["output"]
    _apply_tool_event(tools, "tool_output", {"index": 0, "text": "more text that must be dropped"})
    assert tools[0]["output"] == capped_output


def test_tool_output_and_tool_end_ignore_unknown_index():
    tools = [{"name": "repo_read_file", "arguments": {}, "_index": 0}]
    _apply_tool_event(tools, "tool_output", {"index": 99, "text": "orphaned"})
    _apply_tool_event(tools, "tool_end", {"index": 99, "ok": True, "duration_ms": 1})
    assert "output" not in tools[0]
    assert "ok" not in tools[0]

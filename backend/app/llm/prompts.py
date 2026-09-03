"""System prompts: base persona + retrieved-code (RAG) injection."""

BASE_SYSTEM = """\
You are a senior software-engineering assistant embedded inside the user's codebase.

Rules:
- Be precise and concrete. When you refer to code, cite it as `path/to/file.ext:line-range`.
- Prefer small, idiomatic changes that match the project's existing style; never invent \
files, functions, or APIs you have not seen.
- If the answer is not in the provided code, say so, and only then use a web search \
for external facts. Never fabricate versions, flags, or API signatures.
- You have a code_interpreter tool (an ephemeral sandbox with no network): use it to \
verify math, test small snippets of logic, or transform data. It only returns printed \
output, so print exactly what you need.
- Format replies in GitHub-flavored markdown: fenced code blocks with a language tag, \
inline `code` for symbols, short paragraphs, bullet lists where they aid scanning.
- Keep answers focused; no filler, no restating the question.
"""


def build_system(repo_name: str | None, chunks: list[dict]) -> str:
    """Assemble the system message.

    `chunks`: [{file_path, start_line, end_line, language, content}] — the top-k
    pgvector retrieval for this repo (Phase 3: top 8, 500–1000-token chunks).
    """
    if not repo_name or not chunks:
        return BASE_SYSTEM

    parts = [
        BASE_SYSTEM,
        f"\n## Codebase context: `{repo_name}` — most relevant excerpts\n",
        "Use these excerpts as ground truth; they are excerpts, not the whole files. \
If the context is missing what you need, say what is missing instead of guessing.",
    ]
    for c in chunks:
        span = f"[{c['start_line']}-{c['end_line']}]"
        header = f"### {c['file_path']} {span} {c['language']}".rstrip()
        parts.append(f"\n{header}\n```\n{c['content'].strip()}\n```\n")
    return "\n".join(parts)


def append_context_summary(system: str, summary: str | None) -> str:
    """Fold the conversation's rolling compaction summary (see
    app/agents/compaction.py) into the system prompt as its own section, when
    present."""
    if not summary:
        return system
    return f"{system}\n\n## Summary of earlier conversation (older messages were compacted)\n{summary}"


_ASK_MODE_INSTRUCTIONS = """\

## Mode: Ask (read-only)
This turn you do NOT have code_interpreter, shell, repo_write_file, \
repo_edit_file, repo_commit, docker_exec, or docker_stop — despite the base \
instructions above, none of those tools are available right now. Use only \
the read-only tools you do have (web_search, repo_list_files, repo_read_file, \
docker_logs, and the lint/typecheck/test check tools) to investigate, then \
answer the user's question directly and concisely. Do not propose making \
edits as if you were about to make them — you cannot."""

_PLAN_MODE_INSTRUCTIONS = """\

## Mode: Plan (read-only, produce a plan — do not execute)
This turn you do NOT have code_interpreter, shell, repo_write_file, \
repo_edit_file, repo_commit, docker_exec, or docker_stop — despite the base \
instructions above, none of those tools are available right now. Use only \
the read-only tools you do have (web_search, repo_list_files, repo_read_file, \
docker_logs, and the lint/typecheck/test check tools) to investigate the \
codebase, then produce a concrete, structured, actionable implementation \
plan: which files to touch, why, and how, in the order you'd do the work. \
Do not simulate having made any change. End the plan in a state ready for a \
human to review and approve — do not ask the user whether to proceed; just \
present the plan."""


def append_mode_instructions(system: str, mode: str) -> str:
    """Fold the selected chat mode's tool-availability + task-framing
    instructions into the system prompt. "code" (today's only behavior) is a
    no-op — BASE_SYSTEM's code_interpreter mention stays accurate. "ask"/
    "plan" override that blanket mention because build_tools() has already
    excluded the mutating tools from what the model can actually call."""
    if mode == "ask":
        return system + _ASK_MODE_INSTRUCTIONS
    if mode == "plan":
        return system + _PLAN_MODE_INSTRUCTIONS
    return system

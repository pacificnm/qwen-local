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

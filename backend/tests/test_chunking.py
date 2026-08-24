"""Tests for the language-aware chunker (boundary grouping, fallbacks, skip rules)."""

from app.repos.chunking import (
    OVERLAP_LINES,
    TARGET_TOKENS,
    chunk_source,
    estimate_tokens,
    language_for,
)


def test_estimate_tokens():
    assert estimate_tokens("") >= 1
    assert estimate_tokens("a" * 400) == 100


def test_small_file_is_one_chunk():
    src = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"
    chunks = chunk_source(src, "python")
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == src.count("\n")  # 6 (no trailing newline line)
    assert "def one" in chunks[0].text and "def two" in chunks[0].text


def test_groups_functions_within_budget():
    # 60 functions, each ~16 tokens → whole file well over budget, but each fn small.
    fns = []
    for i in range(60):
        fns.append(f"def fn_{i}(a, b, c):\n    x = a + b * c\n    y = x % 7\n    return y + {i}\n")
    src = "\n".join(fns)
    assert estimate_tokens(src) > TARGET_TOKENS
    chunks = chunk_source(src, "python")

    assert len(chunks) >= 2
    # Every chunk stays inside the spec's 500–1000 token band (a little over the
    # 800 target is fine; summing per-line token estimates truncates per line,
    # so the joined chunk can exceed the target by a few tokens).
    assert all(c.token_estimate <= 1000 for c in chunks)
    # Boundary alignment: each chunk starts exactly at a `def fn_` boundary.
    for c in chunks:
        assert c.text.splitlines()[0].startswith("def fn_"), f"chunk starts mid-statement: {c.text[:60]!r}"
    # No function body is split across chunks.
    for c in chunks:
        first = c.text.splitlines()[0]
        name = first.replace("def ", "").split("(")[0]
        assert f"def {name}" in c.text
        # its `return` lives in the same chunk
        assert "return" in c.text


def test_huge_single_function_is_line_sliced_with_overlap():
    # One function far over budget → the file is line-sliced with OVERLAP_LINES.
    body = "\n".join(f"    x_{i} = {i} * 2  # pad pad pad pad pad pad pad pad" for i in range(900))
    src = f"def huge_fn(a, b):\n{body}\n    return a\n"
    chunks = chunk_source(src, "python")
    assert len(chunks) >= 2
    # Overlap: the last overlap window of chunk i re-appears at the head of chunk i+1.
    tail = chunks[0].text.splitlines()[-OVERLAP_LINES:]
    head = chunks[1].text.splitlines()[:OVERLAP_LINES]
    assert any(line in head for line in tail), "expected an overlapping window between chunks"
    assert all(c.token_estimate <= 1000 for c in chunks)


def test_unparseable_source_falls_back_to_line_split():
    src = "x = ((( this is not python {{{{\n" * 800
    chunks = chunk_source(src, "python")
    assert chunks, "fallback must still produce chunks"
    assert all(c.text.strip() for c in chunks)


def test_javascript_boundaries():
    fns = "\n".join(
        f"function util_{i}(a, b) {{\n  const v = a * b + {i};\n  return v;\n}}\n"
        for i in range(3)
    )
    src = f"import {{ helper }} from './lib';\n\n{fns}\nexport const answer = 42;\n"
    chunks = chunk_source(src, "javascript")
    assert chunks
    combined = "\n".join(c.text for c in chunks)
    assert "function util_0" in combined and "answer = 42" in combined


def test_typescript_small_file_one_chunk():
    src = (
        "interface Props { id: number; name: string; }\n"
        "\n"
        "export function render({ id, name }: Props): string {\n"
        "  return `<div id='${id}'>${name}</div>`;\n"
        "}\n"
    )
    chunks = chunk_source(src, "typescript")
    assert len(chunks) == 1
    assert "render" in chunks[0].text


def test_sql_statements_chunked():
    sql = (
        "SELECT id, title FROM artworks WHERE city = 'Lisbon';\n"
        "\n"
        "SELECT a.id FROM artists a JOIN artworks b ON a.id = b.artist_id;\n"
    )
    chunks = chunk_source(sql, "sql")
    assert chunks
    assert "SELECT" in chunks[0].text


def test_language_for_supported():
    assert language_for("src/index.ts") == "typescript"
    assert language_for("App.tsx") == "tsx"
    assert language_for("server.py") == "python"
    assert language_for("migrations/001_init.sql") == "sql"
    assert language_for("lib/util.mjs") == "javascript"


def test_language_for_skip_rules():
    # directories
    assert language_for("node_modules/lib/x.ts") is None
    assert language_for("dist/bundle.js") is None
    assert language_for(".venv/lib/site.py") is None
    # generated / lock / declaration files
    assert language_for("app.min.js") is None
    if language_for("src/App.d.ts") is not None:
        raise AssertionError("d.ts should be skipped")
    assert language_for("package-lock.json") is None
    assert language_for("src/old.js.map") is None
    # unsupported languages
    assert language_for("main.go") is None
    assert language_for("style.css") is None
    assert language_for("README.md") is None

"""Language-aware chunking for code ingestion (MASTER_SPEC §5, Phase 2).

Boundary-aware: tree-sitter splits at top-level units (functions, classes,
statements) and groups them greedily into ~TARGET_TOKENS. Oversized units and
unparseable files fall back to line slicing with OVERLAP_LINES.
Token counts are estimates (chars/4) — the DB column is token_estimate.
"""

import importlib
import re
from dataclasses import dataclass
from pathlib import Path

TARGET_TOKENS = 800  # spec: 500–1000 token chunks
OVERLAP_LINES = 40
MAX_FILE_BYTES = 1_048_576  # spec edge case: skip files > 1 MB

EXT_LANG = {
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".py": "python",
    ".sql": "sql",
    # Docs/config: no tree-sitter grammar — line-sliced, markdown chunked at
    # headings. Indexing these is what lets the assistant answer questions
    # about READMEs, specs, compose files, etc.
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".sh": "shell",
    ".bash": "shell",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".ini": "ini",
    ".cfg": "ini",
}

#: Extensionless files worth indexing (matched on basename).
_NAME_LANG = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "license": "license",
    "notice": "license",
}

_SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "target", ".next", ".nuxt",
    ".venv", "venv", "__pycache__", "vendor", ".idea", ".vscode", ".cache",
    "coverage", ".pytest_cache", "site-packages",
}

_SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "composer.lock",
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "go.sum", "Gemfile.lock", "uv.lock",
    "deno.lock", "tsconfig.tsbuildinfo",
}

_SKIP_SUFFIX_RE = re.compile(
    r"(\.min\.(js|mjs|css)$|\.map$|\.d\.ts$|_pb2\.py$|\.pb\.(go|cc)$|\.generated\.(js|ts|py)$)",
    re.IGNORECASE,
)


def language_for(path: str) -> str | None:
    """Supported language by extension + skip rules; None when the file is skipped."""
    parts = path.split("/")
    if any(p in _SKIP_DIRS for p in parts[:-1]):
        return None
    name = parts[-1]
    if name in _SKIP_FILES or _SKIP_SUFFIX_RE.search(name):
        return None
    ext = Path(path).suffix.lower()
    return EXT_LANG.get(ext) or _NAME_LANG.get(name.lower())


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class Chunk:
    text: str
    start_line: int  # 1-based inclusive
    end_line: int  # 1-based inclusive
    token_estimate: int


# --- parsers (lazy-loaded; a missing grammar degrades to the line fallback) ---

_PARSERS: dict[str, object] = {}


def _make_parser(language: str) -> object | None:
    entry = {
        "javascript": ("tree_sitter_javascript", "language_javascript"),
        "jsx": ("tree_sitter_javascript", "language_jsx"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "tsx": ("tree_sitter_typescript", "language_tsx"),
        "python": ("tree_sitter_python", "language_python"),
        "sql": ("tree_sitter_sql", "language_sql"),
    }.get(language)
    if entry is None:  # docs/config languages: line-sliced, never parsed
        return None
    module, attr = entry
    try:
        from tree_sitter import Language, Parser

        grammar = getattr(importlib.import_module(module), attr)()
        parser = Parser()
        parser.set_language(Language(grammar))
        return parser
    except Exception:  # noqa: BLE001 — missing wheel/broken grammar: fall back later
        return None


def _get_parser(language: str) -> object | None:
    if language not in _PARSERS:
        _PARSERS[language] = _make_parser(language)
    return _PARSERS[language]


def _top_level_units(text: str, parser: object) -> list[tuple[int, int]] | None:
    """0-based inclusive line ranges of top-level nodes; None when unparseable."""
    try:
        tree = parser.parse(text.encode("utf-8", "replace"))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    if tree.root_node.has_error:
        return None
    nodes = [n for n in tree.root_node.named_children if n.end_byte > n.start_byte]
    return [(n.start_point[0], n.end_point[0]) for n in nodes] or None


# --- chunk assembly ---

_MD_HEADING_RE = re.compile(r"^#{1,6}\s")


def _markdown_units(lines: list[str]) -> list[tuple[int, int]]:
    """0-based inclusive line ranges per markdown section (heading starts a
    unit; text before the first heading is its own unit)."""
    starts = [i for i, ln in enumerate(lines) if _MD_HEADING_RE.match(ln)]
    if not starts:
        return [(0, len(lines) - 1)] if lines else []
    units: list[tuple[int, int]] = []
    if starts[0] > 0:
        units.append((0, starts[0] - 1))
    for a, b in zip(starts[:-1], starts[1:], strict=True):
        if a < b:
            units.append((a, b - 1))
    units.append((starts[-1], len(lines) - 1))
    return [u for u in units if u[0] <= u[1]]


def _group_units(lines: list[str], units: list[tuple[int, int]]) -> list[Chunk]:
    """Greedily group boundary units into ~TARGET_TOKENS chunks; oversized
    units are line-sliced with overlap."""
    chunks: list[Chunk] = []
    start: int | None = None
    end = 0
    toks = 0

    def flush() -> None:
        nonlocal start, end, toks
        if start is not None:
            chunk = _chunk_lines(lines, start, end)
            if chunk is not None:
                chunks.append(chunk)
        start, end, toks = None, 0, 0

    for s, e in units:
        unit_tokens = estimate_tokens("\n".join(lines[s : e + 1]))
        if toks > 0 and toks + unit_tokens > TARGET_TOKENS:
            flush()
        if unit_tokens > TARGET_TOKENS:
            # A single unit over budget: slice it by lines with overlap.
            flush()
            chunks.extend(_line_sliced(lines, s, e + 1))
            continue
        start = s if start is None else start
        end = e + 1
        toks += unit_tokens
    flush()
    return chunks


def _chunk_lines(lines: list[str], a: int, b: int) -> Chunk | None:
    text = "\n".join(lines[a:b])
    if not text.strip():
        return None
    return Chunk(text=text, start_line=a + 1, end_line=b, token_estimate=estimate_tokens(text))


def _line_sliced(lines: list[str], a: int, b: int) -> list[Chunk]:
    """Slice line-range [a, b) into ~TARGET_TOKENS pieces, overlapping by OVERLAP_LINES."""
    chunks: list[Chunk] = []
    i = a
    while i < b:
        toks = 0
        j = i
        while j < b and toks < TARGET_TOKENS:
            toks += estimate_tokens(lines[j])
            j += 1
        j = max(j, i + 1)
        chunk = _chunk_lines(lines, i, j)
        if chunk is not None:
            chunks.append(chunk)
        if j >= b:
            break
        i = max(j - OVERLAP_LINES, i + 1)
    return chunks


def chunk_source(text: str, language: str) -> list[Chunk]:
    """Split source into chunks; boundary-aware where a parser/structure exists
    (functions/classes for code, headings for markdown), line-sliced otherwise."""
    lines = text.splitlines()
    if not lines or not text.strip():
        return []
    if estimate_tokens(text) <= TARGET_TOKENS:
        chunk = _chunk_lines(lines, 0, len(lines))
        return [chunk] if chunk else []

    units: list[tuple[int, int]] | None = None
    parser = _get_parser(language)
    if parser is not None:
        units = _top_level_units(text, parser)
    if units is None and language == "markdown":
        units = _markdown_units(lines)
    if units:
        grouped = _group_units(lines, units)
        if grouped:
            return grouped

    return _line_sliced(lines, 0, len(lines))

"""Qwen-Agent tools for the assistant turn.

Each class is a ``qwen_agent.tools.base.BaseTool`` subclass: Qwen-Agent's
agent loop ``call()``s them synchronously on its worker thread. The
implementation bridges to the app's async handlers (web search, code
sandbox, gitops) through ``TurnRuntime.wait_async`` and emits the SSE
tool events exactly as the frontend consumes them (see the event
contract docstring in ``app/llm/agent.py``):

    tool_start  {"tool": name, "index": i, "arguments": {…}}
    tool_output {"index": i, "text": ≤64 KB}   (streaming tools may repeat)
    tool_end    {"index": i, "ok": bool, "duration_ms": n}

Repo-bound tools (``repo_*``) are only registered when the conversation
is bound to a repository.
"""

import json
import time
from pathlib import Path

from qwen_agent.tools.base import BaseTool

from app.core.settings import get_settings
from app.llm.tools import (
    ToolContext,
    code_interpreter,
    docker_exec,
    docker_logs,
    docker_stop,
    shell,
    web_search,
)
from app.repos import checks, gitops

from .runtime import TurnRuntime

TOOL_OUTPUT_CAP = 64 * 1024  # per docs/API.md — caps UI text AND model input
LIST_FILES_CAP = 300
READ_FILE_MODEL_CAP = 256 * 1024


def _cap(text: str) -> str:
    if len(text) <= TOOL_OUTPUT_CAP:
        return text
    note = f"\n… [truncated to {TOOL_OUTPUT_CAP // 1024} KB]"
    return text[: TOOL_OUTPUT_CAP - len(note)] + note


def _args_dict(params: object) -> dict:
    """Model arguments arrive as a streamed JSON string (dict in tests)."""
    if isinstance(params, dict):
        return params
    if isinstance(params, str):
        try:
            parsed = json.loads(params)
        except ValueError:
            return {"_parse_error": params[:200]}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}


def _async_emit(rt: TurnRuntime):
    """Async emit for the legacy ToolContext (see `_run`'s note)."""

    async def _emit(name: str, data: dict) -> None:
        await rt.emit(name, data)

    return _emit


class _AppTool(BaseTool):
    """Shared plumbing: SSE events + monotonic index + sync→async bridge.

    `call()` (worker thread) hands `_run` to `wait_async`, so `_run` and
    `invoke` execute ON THE EVENT LOOP — they must `await rt.emit`
    directly, not the sync worker-thread bridge `rt.emit_event`.
    """

    #: True if the handler streams its own ``tool_output`` increments while running.
    streams = False

    def __init__(self, rt: TurnRuntime, cfg: dict | None = None):
        super().__init__(cfg)
        self.rt = rt

    def call(self, params: object, **kwargs) -> str:  # noqa: B027 (abstract override)
        return self.rt.wait_async(self._run(params))

    async def _run(self, params: object) -> str:
        idx = self.rt.tool_index()
        args = _args_dict(params)
        started = time.perf_counter()
        await self.rt.emit("tool_start", {"tool": self.name, "index": idx, "arguments": args})
        ok = True
        output = ""
        try:
            output = await self.invoke(args, idx)
        except Exception as exc:  # tool bugs must not leak out of the turn
            ok = False
            output = f"tool failed: {exc.__class__.__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)
        # Streaming tools already pushed live output; still show failure text.
        if self.streams and not ok:
            await self.rt.emit("tool_output", {"index": idx, "text": _cap(output)})
        if not self.streams:
            await self.rt.emit("tool_output", {"index": idx, "text": _cap(output)})
        await self.rt.emit("tool_end", {"index": idx, "ok": ok, "duration_ms": duration_ms})
        return output

    async def invoke(self, args: dict, idx: int) -> str:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# General tools
# --------------------------------------------------------------------------- #
class WebSearchTool(_AppTool):
    name = "web_search"
    description = (
        "Search the web (self-hosted SearXNG) for current information: documentation, "
        "APIs, libraries, changelogs, versions. Returns the top 6 titles, URLs and "
        "snippets (they cannot be fetched further). Use it ONLY for facts that are "
        "not in the provided codebase; never fabricate versions or API signatures."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A short search query (a few words)."}
        },
        "required": ["query"],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        ctx = ToolContext(emit=_async_emit(self.rt), index=idx, cancel=self.rt.cancel)
        return await web_search(args, ctx)


class CodeInterpreterTool(_AppTool):
    name = "code_interpreter"
    description = (
        "Run a one-shot Python 3 script in a disposable sandbox (NO network; files are "
        "lost after the run) and get stdout/stderr back, streamed live. Use it to "
        "verify math, test small pieces of logic, or transform data. Only printed "
        "output is visible — print exactly what you need."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Complete, self-contained Python script (one-shot, no prior context).",
            }
        },
        "required": ["code"],
    }
    streams = True

    async def invoke(self, args: dict, idx: int) -> str:
        return await code_interpreter(
            args, ToolContext(emit=_async_emit(self.rt), index=idx, cancel=self.rt.cancel)
        )


class ShellTool(_AppTool):
    name = "shell"
    description = (
        "Run a shell command (bash) in a disposable sandbox (NO network; files are "
        "lost after the run) and get stdout/stderr back, streamed live. Use it for "
        "git, package managers, file operations, or any CLI task. Only printed "
        "output is visible."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Complete shell command (one-shot, no prior context).",
            }
        },
        "required": ["command"],
    }
    streams = True

    async def invoke(self, args: dict, idx: int) -> str:
        return await shell(
            args, ToolContext(emit=_async_emit(self.rt), index=idx, cancel=self.rt.cancel)
        )


# --------------------------------------------------------------------------- #
# Repo tools (conversation bound to a GitHub repo)
# --------------------------------------------------------------------------- #
class _RepoTool(_AppTool):
    """Base for tools that operate on the linked repository's working copy."""

    def __init__(self, rt: TurnRuntime, full_name: str, cfg: dict | None = None):
        super().__init__(rt, cfg)
        self.full_name = full_name

    @property
    def repo_dir(self) -> Path:
        ws = Path(get_settings().workspace_dir)
        if not ws.is_absolute():
            ws = Path.cwd() / ws
        return gitops.workspace_repo_dir(ws, self.full_name)

    async def _check_repo(self) -> str | None:
        """Empty string = repo usable; otherwise a model-facing error message."""
        if not (self.repo_dir / ".git").is_dir():
            return (
                "The repository has not been synced in this deployment yet. Ask the "
                "user to run a repo sync first; no file operations are possible."
            )
        return ""


# --------------------------------------------------------------------------- #
# The project's own sandbox container (stop / logs / exec) — the SAME
# persistent qcterm-* container behind the Terminal Dock for this repo, never
# an arbitrary model-supplied container name/image/volumes.
# --------------------------------------------------------------------------- #
class DockerStopTool(_RepoTool):
    name = "docker_stop"
    description = (
        "Stop the project's own sandbox container (the one backing the Terminal "
        "Dock). It restarts fresh — same persistent /workspace — next time the "
        "terminal or another docker_* tool needs it. Does not affect any other "
        "container; there is no way to target a different one."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, args: dict, idx: int) -> str:
        return await docker_stop(
            args, ToolContext(emit=_async_emit(self.rt), index=idx, cancel=self.rt.cancel),
            full_name=self.full_name,
        )


class DockerLogsTool(_RepoTool):
    name = "docker_logs"
    description = (
        "Get the last N lines of the project's own sandbox container's logs "
        "(stdout+stderr) — the one backing the Terminal Dock."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tail": {"type": "integer", "description": "Number of lines (default 100, max 200)."},
        },
        "required": [],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        return await docker_logs(
            args, ToolContext(emit=_async_emit(self.rt), index=idx, cancel=self.rt.cancel),
            full_name=self.full_name,
        )


class DockerExecTool(_RepoTool):
    name = "docker_exec"
    description = (
        "Run a command inside the project's own sandbox container (the one "
        "backing the Terminal Dock) — e.g. to start a dev server, run a DB "
        "migration, or inspect running state. Shares state with anything the "
        "user is doing in the Terminal Dock for this project."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run inside the container."},
        },
        "required": ["command"],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        return await docker_exec(
            args, ToolContext(emit=_async_emit(self.rt), index=idx, cancel=self.rt.cancel),
            full_name=self.full_name,
        )


class RepoListFiles(_RepoTool):
    name = "repo_list_files"
    description = (
        "List files in the linked GitHub repository as repo-relative paths. "
        "Optionally narrow to a directory prefix."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory prefix to narrow the listing (e.g. 'src'). Optional.",
            }
        },
        "required": [],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        err = await self._check_repo()
        if err:
            return err
        prefix = str(args.get("path") or "").strip().strip("/")
        try:
            files = await gitops.list_file_paths(self.repo_dir)
        except gitops.GitError as exc:
            return f"listing files failed: {exc}"
        if prefix:
            files = [f for f in files if f == prefix or f.startswith(prefix + "/")]
        if not files:
            return f"no files found{': under ' + repr(prefix) if prefix else ''}"
        shown = files[:LIST_FILES_CAP]
        lines = [f"- {p}" for p in shown]
        if len(files) > LIST_FILES_CAP:
            lines.append(f"… and {len(files) - LIST_FILES_CAP} more files (use a dir prefix to narrow)")
        return f"{len(files)} files:\n" + "\n".join(lines)


class RepoReadFile(_RepoTool):
    name = "repo_read_file"
    description = "Read one file from the linked GitHub repository (repo-relative path)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path, e.g. 'src/app.py'."}
        },
        "required": ["path"],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        path = str(args.get("path") or "").strip().strip("/")
        if not path:
            return "error: missing 'path'"
        err = await self._check_repo()
        if err:
            return err
        try:
            content = gitops.read_file(self.repo_dir, path)
        except gitops.FileNotFound:
            return f"file not found: {path} (list the repo first with repo_list_files)"
        except gitops.GitError as exc:
            return f"reading failed: {exc}"
        if len(content) > READ_FILE_MODEL_CAP:
            cap = READ_FILE_MODEL_CAP
            content = content[:cap] + f"\n… [file truncated to {cap // 1024} KB]"
        return f"# {path}\n```\n{content}\n```"


class RepoWriteFile(_RepoTool):
    name = "repo_write_file"
    description = (
        "Create or overwrite a file in the linked GitHub repository "
        "(repo-relative path; parent directories are created). The change is "
        "uncommitted — call repo_commit only when the user asked for it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path, e.g. 'notes/todo.md'."},
            "content": {"type": "string", "description": "Full new file content."},
        },
        "required": ["path", "content"],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        path = str(args.get("path") or "").strip().strip("/")
        content = args.get("content")
        if not path:
            return "error: missing 'path'"
        if not isinstance(content, str) or not content:
            return "error: missing 'content'"
        err = await self._check_repo()
        if err:
            return err
        try:
            target = gitops.resolve_safe(self.repo_dir, path)
        except gitops.GitError as exc:
            return f"invalid path: {exc}"
        if target.exists() and not target.is_file():
            return f"{path} is not a file; cannot overwrite it"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"write failed: {exc}"
        return f"wrote {len(content)} chars to {path} (uncommitted)"


class RepoEditFile(_RepoTool):
    name = "repo_edit_file"
    description = (
        "Edit an existing file in the linked GitHub repository by replacing an "
        "exact text snippet (`old_string`) with `new_string`. `old_string` must "
        "appear exactly once unless `replace_all` is true. Read the file first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path."},
            "old_string": {
                "type": "string",
                "description": "Exact existing text to replace (uniquely identifying the spot).",
            },
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence instead of requiring exactly one.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        path = str(args.get("path") or "").strip().strip("/")
        old = args.get("old_string")
        new = args.get("new_string")
        replace_all = bool(args.get("replace_all") or False)
        if not path:
            return "error: missing 'path'"
        if not isinstance(old, str) or not old:
            return "error: missing 'old_string' (exact existing text to replace)"
        if not isinstance(new, str):
            return "error: missing 'new_string'"
        err = await self._check_repo()
        if err:
            return err
        try:
            target = gitops.resolve_safe(self.repo_dir, path)
        except gitops.GitError as exc:
            return f"invalid path: {exc}"
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"file not found: {path} (list the repo first with repo_list_files)"
        except OSError as exc:
            return f"read failed: {exc}"
        count = content.count(old)
        if count == 0:
            return (
                f"edit failed: 'old_string' not found in {path} — read the file and "
                "copy the exact text (whitespace-sensitive) before editing"
            )
        if count > 1 and not replace_all:
            return (
                f"edit failed: 'old_string' occurs {count} times in {path} — add more "
                "surrounding context to make it unique, or set replace_all=true"
            )
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return f"write failed: {exc}"
        n = count if replace_all else 1
        return f"edited {path}: replaced {n} occurrence(s) (uncommitted)"


class RepoCommit(_RepoTool):
    name = "repo_commit"
    description = (
        "Commit all uncommitted changes in the linked repository (created/edited/"
        "deleted files) to a new branch and push it. Use ONLY when the user asks "
        "to commit or open a pull request. Optional: custom branch name, PR."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Conventional-commit style message."},
            "branch": {
                "type": "string",
                "description": "Branch name (default: generated qwen-assist/change-<ts>).",
            },
            "open_pr": {
                "type": "boolean",
                "description": "Also open a pull request against the default branch.",
            },
            "pr_title": {"type": "string", "description": "PR title (defaults to the commit message)."},
            "pr_body": {"type": "string", "description": "PR body (markdown)."},
        },
        "required": ["message"],
    }

    async def invoke(self, args: dict, idx: int) -> str:
        message = str(args.get("message") or "").strip()
        if not message:
            return "error: missing 'message' (the commit message)"
        branch = str(args.get("branch") or "").strip() or None
        open_pr = bool(args.get("open_pr") or False)
        pat = get_settings().github_pat
        ws = Path(get_settings().workspace_dir)
        if not ws.is_absolute():
            ws = Path.cwd() / ws
        try:
            result = await gitops.commit_workspace(
                ws,
                self.full_name,
                pat,
                message=message,
                branch=branch,
                open_pr=open_pr,
                pr_title=str(args.get("pr_title") or ""),
                pr_body=str(args.get("pr_body") or ""),
            )
        except gitops.GitError as exc:
            return f"commit failed: {exc}"
        except gitops.InvalidBranch as exc:
            return f"invalid branch name: {exc}"
        except gitops.GithubApiError as exc:
            return f"GitHub API error: {exc}"
        text = f"committed on branch `{result.branch}` ({result.commit_sha})"
        if result.pr_url:
            text += f"\npull request: {result.pr_url}"
        text += "\nRemember: the change is NOT on the default branch until merged."
        return text


# --------------------------------------------------------------------------- #
# Repo quality-check tools (lint / typecheck / tests)
# --------------------------------------------------------------------------- #
class _CheckTool(_RepoTool):
    """Base for tools that run a quality check against the repo working copy."""

    #: The checks module function to call with the repo dir.
    check_fn = None

    async def invoke(self, args: dict, idx: int) -> str:
        err = await self._check_repo()
        if err:
            return err
        return await self.check_fn(self.repo_dir)


class FrontendLintTool(_CheckTool):
    name = "frontend_lint"
    description = (
        "Run ESLint on the frontend/ directory of the linked repository. "
        "Returns 'no problems found' or the list of lint errors with file:line."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    check_fn = checks.frontend_lint


class FrontendTypecheckTool(_CheckTool):
    name = "frontend_typecheck"
    description = (
        "Run the TypeScript compiler (tsc --noEmit) on the frontend/ directory "
        "of the linked repository. Returns 'no type errors' or the list of "
        "type errors with file:line."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    check_fn = checks.frontend_typecheck


class BackendLintTool(_CheckTool):
    name = "backend_lint"
    description = (
        "Run ruff (Python linter) on the backend/ directory of the linked "
        "repository. Returns 'no lint errors' or the list of issues with file:line."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    check_fn = checks.backend_lint


class BackendTypecheckTool(_CheckTool):
    name = "backend_typecheck"
    description = (
        "Run mypy (Python type checker) on the backend/app/ directory of the "
        "linked repository. Returns 'no type errors' or the list of type errors."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    check_fn = checks.backend_typecheck


class BackendTestsTool(_CheckTool):
    name = "backend_tests"
    description = (
        "Run the backend test suite (pytest) for the linked repository. "
        "Returns 'all tests passed' or the first failure with a short traceback."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    check_fn = checks.backend_tests


def build_tools(rt: TurnRuntime, repo: object | None) -> list[_AppTool]:
    """The turn's toolset; repo tools only when the conversation is bound to a repo."""
    tools: list[_AppTool] = [
        WebSearchTool(rt),
        CodeInterpreterTool(rt),
        ShellTool(rt),
    ]
    full_name = getattr(repo, "github_full_name", None)
    if full_name:
        tools += [
            DockerStopTool(rt, full_name),
            DockerLogsTool(rt, full_name),
            DockerExecTool(rt, full_name),
            RepoListFiles(rt, full_name),
            RepoReadFile(rt, full_name),
            RepoWriteFile(rt, full_name),
            RepoEditFile(rt, full_name),
            RepoCommit(rt, full_name),
            FrontendLintTool(rt, full_name),
            FrontendTypecheckTool(rt, full_name),
            BackendLintTool(rt, full_name),
            BackendTypecheckTool(rt, full_name),
            BackendTestsTool(rt, full_name),
        ]
    return tools

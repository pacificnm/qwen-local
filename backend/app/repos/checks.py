"""Repo quality checks: frontend lint, frontend typecheck, backend Python checks.

Each function runs the appropriate CLI tool via `docker exec` INSIDE the
project's OWN sandbox container (the same `qcterm-*` container behind the
Terminal Dock, resolved through `resolve_project_container` — never a
model-supplied name). That keeps arbitrary repo-controlled code (test files,
`conftest.py`, lint/typecheck invocations) isolated from the backend's own
process — the same boundary `code_interpreter`/`shell`/`docker_exec` already
hold — instead of running it with the backend's own privileges (`.env`
secrets, DB credentials, GitHub PAT, Docker socket group membership).

Design notes:
- The tool names ("frontend"/"backend") are just labels for "the JS/TS
  side" and "the Python side" of a repo — NOT a hardcoded `frontend/`/
  `backend/` directory. Linked repos vary (this app's own `backend/` +
  `frontend/`, but also `server/` + `client/`, a flat single-project repo,
  ...), so `_find_project_dir` locates the actual directory by looking for
  a `package.json` / `pyproject.toml`-etc. signal file instead of assuming
  a fixed name. Regression: an earlier version hardcoded "backend"/
  "frontend" and broke on every other layout ("No such file or directory").
- The sandbox image (sandbox/Dockerfile) bakes in Node.js so the frontend
  checks work out of the box; a container started from an older image
  self-heals (`_ensure_npx` installs Node on demand, once, via apt) instead
  of just failing "command not found" forever.
- The frontend checks also self-heal `node_modules` (`_ensure_frontend_deps`,
  `npm ci`/`npm install`) — without it, bare `npx eslint`/`npx tsc` find
  nothing local and npx fetches an unrelated package from the registry
  instead (most confusingly, the long-deprecated standalone `tsc` npm
  package rather than the real TypeScript compiler inside `typescript`).
- Python dev tooling (ruff/mypy/pytest) is NOT baked in or auto-installed —
  a missing binary there surfaces as a normal shell "command not found".
- All commands run with a timeout to avoid hanging the turn.
"""

from __future__ import annotations

from app.sandbox import DockerError, TerminalError, get_docker_manager, resolve_project_container

#: Max seconds any single check may run before we kill it.
CHECK_TIMEOUT = 120.0
#: pytest runs can take longer than the others.
TEST_TIMEOUT = 180.0
#: The one-time Node.js install (apt update + install) can be slow.
NODE_INSTALL_TIMEOUT = 180.0
#: `npm ci`/`npm install` on a real frontend's full dependency tree is slower still.
NPM_INSTALL_TIMEOUT = 300.0

#: Cap on returned output (chars) to keep the model context manageable.
OUTPUT_CAP = 32 * 1024  # 32 KB


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    note = f"\n… [truncated to {OUTPUT_CAP // 1024} KB]"
    return text[: OUTPUT_CAP - len(note)] + note


#: Signal files that mark a directory as "the JS/TS project" / "the Python
#: project" — existence is checked, not parsed. Common layout names are
#: preferred when a signal shows up in more than one candidate directory,
#: purely to pick a stable, unsurprising choice; any match is used otherwise.
_FRONTEND_SIGNALS = ("package.json",)
_BACKEND_SIGNALS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")
_PREFERRED_FRONTEND_DIRS = ("frontend", "client", "web", "ui", "app")
_PREFERRED_BACKEND_DIRS = ("backend", "server", "api")


def _parent_dir(find_line: str) -> str:
    """'./frontend/package.json' -> 'frontend'; './package.json' -> '.'."""
    path = find_line.removeprefix("./")
    return path.rsplit("/", 1)[0] if "/" in path else "."


async def _find_project_dir(
    name: str, cwd: str, *, signals: tuple[str, ...], preferred: tuple[str, ...]
) -> str | None:
    """Locate the subdirectory (relative to the repo root `cwd`) that looks
    like the JS/TS or Python project, by signal-file presence rather than a
    fixed directory name — repos vary (`backend/`+`frontend/`, `server/`+
    `client/`, a flat single-project repo, ...). Depth 2 covers the repo
    root itself and one level of subdirectory. Returns `"."` for the root,
    a relative subdir name, or `None` when no signal file is found anywhere.

    A named subdirectory wins over the repo root whenever both qualify: a
    root-level package.json/pyproject.toml alongside one or more project
    subdirectories is commonly just a monorepo/workspace wrapper with no
    real lint/type config of its own (confirmed against a real repo: its
    root package.json has no matching eslint config, while client/ and
    server/ each have their own) — root is used only when it's the sole
    candidate found.
    """
    mgr = get_docker_manager()
    name_predicate = " -o ".join(f'-name "{s}"' for s in signals)
    cmd = (
        f"find . -maxdepth 2 \\( {name_predicate} \\) "
        "-not -path '*/node_modules/*' -not -path '*/.git/*'"
    )
    rc, out, _err = await mgr.exec(name, cmd, workdir=cwd, timeout=15)
    if rc != 0 or not out.strip():
        return None
    candidates = {_parent_dir(line) for line in out.strip().splitlines() if line.strip()}
    for pref in preferred:
        if pref in candidates:
            return pref
    subdirs = candidates - {"."}
    if subdirs:
        return min(subdirs)
    return "." if "." in candidates else None


async def _resolve_repo_container(full_name: str) -> tuple[str, str] | str:
    """(name, cwd) for the project's own sandbox container, or a model-facing
    error string if it can't be reached or the repo isn't synced there."""
    try:
        name, cwd = await resolve_project_container(full_name)
    except TerminalError as exc:
        return f"could not reach the project's sandbox container: {exc}"
    if cwd != "/repo":
        return "the repository has not been synced in this deployment yet — no files to check"
    return name, cwd


async def _ensure_npx(name: str) -> str | None:
    """Best-effort: install Node.js (npx included) if the container predates
    the sandbox image baking it in (sandbox/Dockerfile). Returns a
    model-facing error string on failure, else None (npx is ready)."""
    mgr = get_docker_manager()
    rc, _, _ = await mgr.exec(name, "command -v npx", timeout=10)
    if rc == 0:
        return None
    rc, out, err = await mgr.exec(
        name,
        "sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm",
        timeout=NODE_INSTALL_TIMEOUT,
    )
    if rc != 0:
        return _cap(f"npx is not installed and the automatic install failed:\n{(err or out).strip()}")
    return None


async def _ensure_frontend_deps(name: str, fe: str) -> str | None:
    """Best-effort: `npm ci`/`npm install` in the detected JS/TS project dir
    if `node_modules` is missing (a fresh clone never has it). Without this,
    `npx eslint`/`npx tsc` find nothing local and npx silently fetches an
    UNRELATED package from the registry instead — most notably the
    long-deprecated standalone `tsc` npm package (not the TypeScript
    compiler, which ships inside `typescript`), producing a confusing "not
    the tsc command you are looking for" result instead of an actual
    typecheck. Returns a model-facing error string on failure, else None
    (node_modules is ready)."""
    mgr = get_docker_manager()
    rc, _, _ = await mgr.exec(name, "test -d node_modules", workdir=fe, timeout=10)
    if rc == 0:
        return None
    rc, out, err = await mgr.exec(
        name,
        "if [ -f package-lock.json ]; then npm ci; else npm install; fi",
        workdir=fe,
        timeout=NPM_INSTALL_TIMEOUT,
    )
    if rc != 0:
        return _cap(f"npm install failed:\n{(err or out).strip()}")
    return None


async def _run_frontend_check(full_name: str, command: str) -> tuple[int, str]:
    """Locate the JS/TS project directory (see `_find_project_dir`), install
    Node.js if `npx` is missing (a container started from an older sandbox
    image) and the project's own dependencies if `node_modules` is missing,
    then run `command` there. Returns (returncode, combined stdout+stderr)."""
    resolved = await _resolve_repo_container(full_name)
    if isinstance(resolved, str):
        return 1, resolved
    name, cwd = resolved
    subdir = await _find_project_dir(
        name, cwd, signals=_FRONTEND_SIGNALS, preferred=_PREFERRED_FRONTEND_DIRS
    )
    if subdir is None:
        return 1, "no package.json found (checked the repo root and one level of subdirectory)"
    fe = f"{cwd}/{subdir}" if subdir != "." else cwd
    install_err = await _ensure_npx(name)
    if install_err:
        return 1, install_err
    deps_err = await _ensure_frontend_deps(name, fe)
    if deps_err:
        return 1, deps_err
    mgr = get_docker_manager()
    try:
        rc, out, err = await mgr.exec(name, command, workdir=fe, timeout=CHECK_TIMEOUT)
    except DockerError as exc:
        return 1, str(exc)
    combined = out + (("\n" + err) if err else "")
    return rc, f"(ran in {subdir}/)\n{combined}" if subdir != "." else combined


# --------------------------------------------------------------------------- #
# Frontend checks
# --------------------------------------------------------------------------- #


async def frontend_lint(full_name: str) -> str:
    """Run ESLint on the repo's JS/TS project directory."""
    rc, out = await _run_frontend_check(full_name, "npx eslint --max-warnings=0 .")
    if rc == 0:
        return "eslint: no problems found ✓"
    return _cap(f"eslint exited {rc}:\n{out}")


async def frontend_typecheck(full_name: str) -> str:
    """Run the TypeScript compiler (tsc --noEmit) on the repo's JS/TS project directory."""
    rc, out = await _run_frontend_check(full_name, "npx tsc --noEmit")
    if rc == 0:
        return "tsc: no type errors found ✓"
    return _cap(f"tsc exited {rc}:\n{out}")


# --------------------------------------------------------------------------- #
# Backend (Python) checks
# --------------------------------------------------------------------------- #


async def _run_backend_check(full_name: str, command: str, *, timeout: float = CHECK_TIMEOUT) -> tuple[int, str]:
    """Like `_run_frontend_check`, but for the repo's Python project
    directory (see `_find_project_dir`) — no dependency self-heal, since
    Python tooling isn't baked into the sandbox image."""
    resolved = await _resolve_repo_container(full_name)
    if isinstance(resolved, str):
        return 1, resolved
    name, cwd = resolved
    subdir = await _find_project_dir(
        name, cwd, signals=_BACKEND_SIGNALS, preferred=_PREFERRED_BACKEND_DIRS
    )
    if subdir is None:
        return 1, (
            "no Python project found (looked for pyproject.toml / requirements.txt / "
            "setup.py / setup.cfg at the repo root and one level of subdirectory)"
        )
    be = f"{cwd}/{subdir}" if subdir != "." else cwd
    mgr = get_docker_manager()
    try:
        rc, out, err = await mgr.exec(name, command, workdir=be, timeout=timeout)
    except DockerError as exc:
        return 1, str(exc)
    combined = out + (("\n" + err) if err else "")
    return rc, f"(ran in {subdir}/)\n{combined}" if subdir != "." else combined


async def backend_lint(full_name: str) -> str:
    """Run ruff (lint) on the repo's Python project directory."""
    rc, out = await _run_backend_check(full_name, "ruff check --output-format=concise .")
    if rc == 0:
        return "ruff: no lint errors found ✓"
    return _cap(f"ruff exited {rc}:\n{out}")


async def backend_typecheck(full_name: str) -> str:
    """Run mypy on the repo's Python project directory."""
    rc, out = await _run_backend_check(full_name, "mypy --ignore-missing-imports .")
    if rc == 0:
        return "mypy: no type errors found ✓"
    return _cap(f"mypy exited {rc}:\n{out}")


async def backend_tests(full_name: str) -> str:
    """Run pytest on the repo's Python project directory (auto-discovers
    tests — no assumption of a `tests/` subfolder name)."""
    rc, out = await _run_backend_check(
        full_name, "python3 -m pytest -x -q --tb=short", timeout=TEST_TIMEOUT
    )
    if rc == 0:
        return "pytest: all tests passed ✓"
    return _cap(f"pytest exited {rc}:\n{out}")

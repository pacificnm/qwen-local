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
- The sandbox image ships neither Node.js nor Python dev tooling by default;
  a missing binary surfaces as a normal shell "command not found" in the
  captured output rather than a special-cased error.
- All commands run with a timeout to avoid hanging the turn.
"""

from __future__ import annotations

from app.sandbox import DockerError, TerminalError, get_docker_manager, resolve_project_container

#: Max seconds any single check may run before we kill it.
CHECK_TIMEOUT = 120.0
#: pytest runs can take longer than the others.
TEST_TIMEOUT = 180.0

#: Cap on returned output (chars) to keep the model context manageable.
OUTPUT_CAP = 32 * 1024  # 32 KB


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    note = f"\n… [truncated to {OUTPUT_CAP // 1024} KB]"
    return text[: OUTPUT_CAP - len(note)] + note


async def _run_in_project_container(
    full_name: str, subdir: str, command: str, *, timeout: float = CHECK_TIMEOUT
) -> tuple[int, str]:
    """Run `command` at `/repo/<subdir>` inside the project's own sandbox
    container. Returns (returncode, combined stdout+stderr)."""
    try:
        name, cwd = await resolve_project_container(full_name)
    except TerminalError as exc:
        return 1, f"could not reach the project's sandbox container: {exc}"
    if cwd != "/repo":
        return 1, "the repository has not been synced in this deployment yet — no files to check"
    mgr = get_docker_manager()
    try:
        rc, out, err = await mgr.exec(name, command, workdir=f"{cwd}/{subdir}", timeout=timeout)
    except DockerError as exc:
        return 1, str(exc)
    return rc, out + (("\n" + err) if err else "")


# --------------------------------------------------------------------------- #
# Frontend checks
# --------------------------------------------------------------------------- #


async def frontend_lint(full_name: str) -> str:
    """Run ESLint on the frontend/ directory."""
    rc, out = await _run_in_project_container(full_name, "frontend", "npx eslint --max-warnings=0 .")
    if rc == 0:
        return "eslint: no problems found ✓"
    return _cap(f"eslint exited {rc}:\n{out}")


async def frontend_typecheck(full_name: str) -> str:
    """Run the TypeScript compiler (tsc --noEmit) on the frontend/ directory."""
    rc, out = await _run_in_project_container(full_name, "frontend", "npx tsc --noEmit")
    if rc == 0:
        return "tsc: no type errors found ✓"
    return _cap(f"tsc exited {rc}:\n{out}")


# --------------------------------------------------------------------------- #
# Backend (Python) checks
# --------------------------------------------------------------------------- #


async def backend_lint(full_name: str) -> str:
    """Run ruff (lint) on the backend/ directory."""
    rc, out = await _run_in_project_container(full_name, "backend", "ruff check --output-format=concise .")
    if rc == 0:
        return "ruff: no lint errors found ✓"
    return _cap(f"ruff exited {rc}:\n{out}")


async def backend_typecheck(full_name: str) -> str:
    """Run mypy on the backend/app/ directory."""
    rc, out = await _run_in_project_container(full_name, "backend", "mypy --ignore-missing-imports app/")
    if rc == 0:
        return "mypy: no type errors found ✓"
    return _cap(f"mypy exited {rc}:\n{out}")


async def backend_tests(full_name: str) -> str:
    """Run pytest on the backend/tests/ directory."""
    rc, out = await _run_in_project_container(
        full_name, "backend", "python3 -m pytest tests/ -x -q --tb=short", timeout=TEST_TIMEOUT
    )
    if rc == 0:
        return "pytest: all tests passed ✓"
    return _cap(f"pytest exited {rc}:\n{out}")

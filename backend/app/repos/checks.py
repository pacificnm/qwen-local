"""Repo quality checks: frontend lint, frontend typecheck, backend Python checks.

Each function runs the appropriate CLI tool as a subprocess in the backend
process, targeting the repo's working copy. Output is captured and returned
as a single string (stdout + stderr interleaved, capped).

Design notes:
- Frontend tools (eslint, tsc) require Node.js on the backend host. If it's
  missing, the function returns a clear diagnostic rather than raising.
- Backend tools (pytest, ruff) require the Python packages to be installed.
  Same graceful-degradation pattern.
- All commands run with a timeout to avoid hanging the turn.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Max seconds any single check may run before we kill it.
CHECK_TIMEOUT = 120.0

#: Cap on returned output (chars) to keep the model context manageable.
OUTPUT_CAP = 32 * 1024  # 32 KB


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    note = f"\n… [truncated to {OUTPUT_CAP // 1024} KB]"
    return text[: OUTPUT_CAP - len(note)] + note


def _binary(name: str) -> str | None:
    """Return the absolute path to `name` if found on PATH, else None."""
    return shutil.which(name)


async def _run(
    cmd: list[str],
    cwd: Path,
    timeout: float = CHECK_TIMEOUT,
) -> tuple[int, str]:
    """Run `cmd` in `cwd`; return (returncode, combined stdout+stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # interleave
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {timeout:.0f}s"
    text = out.decode("utf-8", "replace") if out else ""
    return (proc.returncode or 0), text


# --------------------------------------------------------------------------- #
# Frontend checks
# --------------------------------------------------------------------------- #

async def frontend_lint(repo_dir: Path) -> str:
    """Run ESLint on the frontend/ directory."""
    fe = repo_dir / "frontend"
    if not fe.is_dir():
        return "error: no frontend/ directory in this repo"
    node = _binary("npx")
    if not node:
        return "error: npx not found on PATH — Node.js is not installed in this environment"
    rc, out = await _run([node, "eslint", "--max-warnings=0", "."], cwd=fe)
    if rc == 0:
        return "eslint: no problems found ✓"
    return _cap(f"eslint exited {rc}:\n{out}")


async def frontend_typecheck(repo_dir: Path) -> str:
    """Run TypeScript compiler (tsc --noEmit) on the frontend/ directory."""
    fe = repo_dir / "frontend"
    if not fe.is_dir():
        return "error: no frontend/ directory in this repo"
    node = _binary("npx")
    if not node:
        return "error: npx not found on PATH — Node.js is not installed in this environment"
    rc, out = await _run([node, "tsc", "--noEmit"], cwd=fe)
    if rc == 0:
        return "tsc: no type errors found ✓"
    return _cap(f"tsc exited {rc}:\n{out}")


# --------------------------------------------------------------------------- #
# Backend (Python) checks
# --------------------------------------------------------------------------- #

async def backend_lint(repo_dir: Path) -> str:
    """Run ruff (lint) on the backend/ directory."""
    be = repo_dir / "backend"
    if not be.is_dir():
        return "error: no backend/ directory in this repo"
    ruff = _binary("ruff")
    if not ruff:
        return "error: ruff not found on PATH — install with `pip install ruff`"
    rc, out = await _run([ruff, "check", "--output-format=concise", "."], cwd=be)
    if rc == 0:
        return "ruff: no lint errors found ✓"
    return _cap(f"ruff exited {rc}:\n{out}")


async def backend_typecheck(repo_dir: Path) -> str:
    """Run mypy on the backend/ directory."""
    be = repo_dir / "backend"
    if not be.is_dir():
        return "error: no backend/ directory in this repo"
    mypy = _binary("mypy")
    if not mypy:
        return "error: mypy not found on PATH — install with `pip install mypy`"
    rc, out = await _run([mypy, "--ignore-missing-imports", "app/"], cwd=be)
    if rc == 0:
        return "mypy: no type errors found ✓"
    return _cap(f"mypy exited {rc}:\n{out}")


async def backend_tests(repo_dir: Path) -> str:
    """Run pytest on the backend/ directory."""
    be = repo_dir / "backend"
    if not be.is_dir():
        return "error: no backend/ directory in this repo"
    python = _binary("python3") or _binary("python")
    if not python:
        return "error: python not found on PATH"
    rc, out = await _run(
        [python, "-m", "pytest", "tests/", "-x", "-q", "--tb=short"],
        cwd=be,
        timeout=180.0,  # tests can take longer
    )
    if rc == 0:
        return "pytest: all tests passed ✓"
    return _cap(f"pytest exited {rc}:\n{out}")

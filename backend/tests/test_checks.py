"""Repo quality-check auto-detection tests (no real docker/network).

Regression coverage for the bug where frontend_lint/frontend_typecheck/
backend_lint/backend_typecheck/backend_tests hardcoded "frontend"/"backend"
as literal subdirectory names — breaking on any repo with a different
layout (e.g. server/+client/) with a bare "No such file or directory".
`_find_project_dir` now locates the project by signal-file presence
(package.json / pyproject.toml etc.) instead.
"""

from app.repos import checks


class FakeManager:
    """`get_docker_manager()` stand-in: rules are (predicate, (rc, out, err))
    pairs checked in order; the first predicate matching `command` wins."""

    def __init__(self, rules):
        self.rules = rules
        self.calls: list[tuple[str, str | None]] = []

    async def exec(self, _name, command, *, workdir=None, timeout=None):
        self.calls.append((command, workdir))
        for predicate, result in self.rules:
            if predicate(command):
                return result
        raise AssertionError(f"unexpected command: {command!r} (workdir={workdir!r})")


async def _fake_resolve(_full_name):
    return "qcterm-test", "/repo"


def _install(monkeypatch, fake: FakeManager):
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    monkeypatch.setattr(checks, "resolve_project_container", _fake_resolve)


# --- _find_project_dir --------------------------------------------------------


async def test_find_project_dir_uses_root_when_sole_candidate(monkeypatch):
    fake = FakeManager([(lambda c: "package.json" in c, (0, "./package.json\n", ""))])
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    result = await checks._find_project_dir(
        "n", "/repo", signals=checks._FRONTEND_SIGNALS, preferred=checks._PREFERRED_FRONTEND_DIRS
    )
    assert result == "."


async def test_find_project_dir_prefers_named_subdir_over_root(monkeypatch):
    """Regression pin (real repo): a root package.json alongside client/ and
    server/ package.jsons is a monorepo wrapper — root itself has no eslint
    config while client/ and server/ each have their own, so a named
    subdirectory must win over root whenever both qualify."""
    fake = FakeManager([
        (lambda c: "package.json" in c,
         (0, "./package.json\n./client/package.json\n./server/package.json\n", "")),
    ])
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    result = await checks._find_project_dir(
        "n", "/repo", signals=checks._FRONTEND_SIGNALS, preferred=checks._PREFERRED_FRONTEND_DIRS
    )
    assert result == "client"


async def test_find_project_dir_prefers_known_name(monkeypatch):
    fake = FakeManager([
        (lambda c: "package.json" in c,
         (0, "./random/package.json\n./client/package.json\n", "")),
    ])
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    result = await checks._find_project_dir(
        "n", "/repo", signals=checks._FRONTEND_SIGNALS, preferred=checks._PREFERRED_FRONTEND_DIRS
    )
    assert result == "client"


async def test_find_project_dir_falls_back_alphabetically(monkeypatch):
    fake = FakeManager([
        (lambda c: "package.json" in c,
         (0, "./zzz/package.json\n./aaa/package.json\n", "")),
    ])
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    result = await checks._find_project_dir(
        "n", "/repo", signals=checks._FRONTEND_SIGNALS, preferred=checks._PREFERRED_FRONTEND_DIRS
    )
    assert result == "aaa"


async def test_find_project_dir_prefers_unnamed_subdir_over_root(monkeypatch):
    """Even an unrecognized subdirectory name beats a root that's also just
    one candidate among several — root only wins when it's the ONLY hit."""
    fake = FakeManager([
        (lambda c: "package.json" in c,
         (0, "./package.json\n./oddname/package.json\n", "")),
    ])
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    result = await checks._find_project_dir(
        "n", "/repo", signals=checks._FRONTEND_SIGNALS, preferred=checks._PREFERRED_FRONTEND_DIRS
    )
    assert result == "oddname"


async def test_find_project_dir_returns_none_when_nothing_found(monkeypatch):
    fake = FakeManager([(lambda c: "package.json" in c, (0, "", ""))])
    monkeypatch.setattr(checks, "get_docker_manager", lambda: fake)
    result = await checks._find_project_dir(
        "n", "/repo", signals=checks._FRONTEND_SIGNALS, preferred=checks._PREFERRED_FRONTEND_DIRS
    )
    assert result is None


# --- frontend_lint / frontend_typecheck: server+client-style layout ---------


def _frontend_rules(subdir: str, check_rc: int = 0, check_out: str = ""):
    return [
        (lambda c: "package.json" in c, (0, f"./{subdir}/package.json\n", "")),
        (lambda c: c.startswith("command -v npx"), (0, "/usr/bin/npx\n", "")),
        (lambda c: c.startswith("test -d node_modules"), (0, "", "")),
        (lambda c: "eslint" in c or "tsc" in c, (check_rc, check_out, "")),
    ]


async def test_frontend_lint_detects_client_dir(monkeypatch):
    fake = FakeManager(_frontend_rules("client"))
    _install(monkeypatch, fake)

    result = await checks.frontend_lint("o/reactamp")

    assert result == "eslint: no problems found ✓"
    eslint_call = next(c for c in fake.calls if "eslint" in c[0])
    assert eslint_call[1] == "/repo/client"


async def test_frontend_typecheck_reports_client_dir_on_failure(monkeypatch):
    fake = FakeManager(_frontend_rules("client", check_rc=1, check_out="src/App.tsx(1,1): error TS1"))
    _install(monkeypatch, fake)

    result = await checks.frontend_typecheck("o/reactamp")

    assert "tsc exited 1" in result
    assert "(ran in client/)" in result
    assert "App.tsx" in result


async def test_frontend_lint_no_package_json_found(monkeypatch):
    fake = FakeManager([(lambda c: "package.json" in c, (0, "", ""))])
    _install(monkeypatch, fake)

    result = await checks.frontend_lint("o/no-js")

    assert "no package.json found" in result


# --- backend_lint / backend_typecheck / backend_tests: server-style layout --


def _backend_rules(subdir: str, check_rc: int = 0, check_out: str = ""):
    return [
        (lambda c: "pyproject.toml" in c, (0, f"./{subdir}/pyproject.toml\n", "")),
        (lambda c: "ruff" in c or "mypy" in c or "pytest" in c, (check_rc, check_out, "")),
    ]


async def test_backend_lint_detects_server_dir(monkeypatch):
    fake = FakeManager(_backend_rules("server"))
    _install(monkeypatch, fake)

    result = await checks.backend_lint("o/reactamp")

    assert result == "ruff: no lint errors found ✓"
    ruff_call = next(c for c in fake.calls if "ruff" in c[0])
    assert ruff_call[1] == "/repo/server"


async def test_backend_tests_reports_server_dir_on_failure(monkeypatch):
    fake = FakeManager(_backend_rules("server", check_rc=1, check_out="FAILED tests/test_x.py"))
    _install(monkeypatch, fake)

    result = await checks.backend_tests("o/reactamp")

    assert "pytest exited 1" in result
    assert "(ran in server/)" in result


async def test_backend_lint_no_python_project_found(monkeypatch):
    fake = FakeManager([(lambda c: "pyproject.toml" in c, (0, "", ""))])
    _install(monkeypatch, fake)

    result = await checks.backend_lint("o/no-python")

    assert "no Python project found" in result

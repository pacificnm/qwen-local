"""Agent tools exposed to the model.

Phase 3: web_search (SearXNG). Phase 5: code_interpreter (ephemeral Docker
sandbox; its stdout/stderr stream incrementally into `tool_output` events).

Handler contract: `async def handler(arguments: dict, ctx: ToolContext) -> str`.
`ToolContext` carries the loop's `emit` and the user-Stop `cancel` event. A
tool marked `streams=True` pushes its own incremental `tool_output` events
while running and the agent loop suppresses the final `tool_output`; the
string it returns is always what the model sees as the tool result.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.core.settings import get_settings
from app.sandbox import DockerError, SandboxManager, get_docker_manager

MAX_RESULTS = 6
MAX_SNIPPET_CHARS = 400
#: What the model may see per tool result (the agent loop caps at 64 KB anyway).
MAX_TOOL_OUTPUT_CHARS = 32 * 1024
SearchTimeout = httpx.Timeout(15.0, connect=5.0)

Emit = Callable[[str, dict], Awaitable[None]]
Handler = Callable[[dict, "ToolContext"], Awaitable[str]]


@dataclass
class ToolContext:
    """Runtime context handed to every tool handler.

    - `emit`: push incremental `tool_output` events (streaming tools only)
    - `index`: this call's index within the current assistant turn
    - `cancel`: user Stop — long-running handlers must observe it
    """

    emit: Emit
    index: int
    cancel: asyncio.Event


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments object
    handler: Handler
    #: True if the handler streams its own `tool_output` increments while running.
    streams: bool = False

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# --------------------------------------------------------------------------- #
# web search
# --------------------------------------------------------------------------- #
async def web_search(arguments: dict, _ctx: ToolContext) -> str:
    """Query self-hosted SearXNG (JSON API) and format the top hits."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return "error: missing 'query'"
    settings = get_settings()
    url = f"{settings.searxng_url.rstrip('/')}/search"
    try:
        async with httpx.AsyncClient(timeout=SearchTimeout) as client:
            res = await client.get(url, params={"q": query, "format": "json"})
            res.raise_for_status()
            rows = res.json().get("results") or []
    except httpx.HTTPError as exc:
        return f"web search unavailable ({exc.__class__.__name__}); answer from knowledge instead"

    if not rows:
        return f"no results for: {query}"

    lines: list[str] = []
    for i, row in enumerate(rows[:MAX_RESULTS], start=1):
        title = str(row.get("title") or row.get("url") or "untitled").strip()
        link = str(row.get("url") or "")
        snippet = " ".join(str(row.get("content") or "").split())
        if len(snippet) > MAX_SNIPPET_CHARS:
            snippet = snippet[:MAX_SNIPPET_CHARS].rstrip() + "…"
        lines.append(f"{i}. {title} — {link}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# code interpreter (ephemeral sandbox, spec §4.5)
# --------------------------------------------------------------------------- #
_manager: SandboxManager | None = None


def get_sandbox_manager() -> SandboxManager:
    """Process-wide manager; tests may monkey-patch this (see reset_sandbox_manager)."""
    global _manager
    if _manager is None:
        s = get_settings()
        _manager = SandboxManager(
            image_name=s.sandbox_image_name,
            timeout_seconds=s.sandbox_timeout_seconds,
            memory=s.sandbox_memory,
            cpus=s.sandbox_cpus,
            network=s.sandbox_network,
            user=s.sandbox_user,
            tmpfs=s.sandbox_tmpfs,
            pids_limit=s.sandbox_pids_limit,
        )
    return _manager


def reset_sandbox_manager() -> None:
    """Drop the cached manager (test seam)."""
    global _manager
    _manager = None


async def code_interpreter(arguments: dict, ctx: ToolContext) -> str:
    """Run Python in a fresh hardened sandbox; stream output live via `ctx.emit`."""
    code = str(arguments.get("code") or "").strip()
    if not code:
        raise ValueError("code_interpreter requires a non-empty 'code'")

    mgr = get_sandbox_manager()
    settings = get_settings()
    await mgr.ensure_image(settings.sandbox_build_context)

    async def on_output(text: str) -> None:
        await ctx.emit("tool_output", {"index": ctx.index, "text": text})

    res = await mgr.run(code, on_output=on_output, cancel=ctx.cancel)

    parts = [f"exit code: {res.exit_code}"]
    if res.timed_out:
        parts.append(f"TIMEOUT: killed after the hard limit ({mgr.timeout_seconds}s)")
    if res.cancelled:
        parts.append("CANCELLED: the user pressed Stop")
    if res.stdout:
        parts.append("STDOUT:\n" + res.stdout)
    if res.stderr:
        parts.append("STDERR:\n" + res.stderr)
    if not res.stdout and not res.stderr and not res.stdout_truncated and not res.stderr_truncated:
        parts.append("(no output)")
    text = "\n\n".join(parts)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n… [result truncated]"
    return text


async def shell(arguments: dict, ctx: ToolContext) -> str:
    """Run a shell command in a fresh hardened sandbox; stream output live."""
    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValueError("shell requires a non-empty 'command'")

    mgr = get_sandbox_manager()
    settings = get_settings()
    await mgr.ensure_image(settings.sandbox_build_context)

    async def on_output(text: str) -> None:
        await ctx.emit("tool_output", {"index": ctx.index, "text": text})

    res = await mgr.run(
        command,
        on_output=on_output,
        cancel=ctx.cancel,
        command=["bash", "-c", command],
    )

    parts = [f"exit code: {res.exit_code}"]
    if res.timed_out:
        parts.append(f"TIMEOUT: killed after the hard limit ({mgr.timeout_seconds}s)")
    if res.cancelled:
        parts.append("CANCELLED: the user pressed Stop")
    if res.stdout:
        parts.append("STDOUT:\n" + res.stdout)
    if res.stderr:
        parts.append("STDERR:\n" + res.stderr)
    if not res.stdout and not res.stderr and not res.stdout_truncated and not res.stderr_truncated:
        parts.append("(no output)")
    text = "\n\n".join(parts)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n… [result truncated]"
    return text


# --------------------------------------------------------------------------- #
# docker container management (create / list / stop / remove / logs / exec)
# --------------------------------------------------------------------------- #
async def docker_create(arguments: dict, _ctx: ToolContext) -> str:
    """Create and start a named container."""
    name = str(arguments.get("name") or "").strip()
    image = str(arguments.get("image") or "").strip()
    if not name or not image:
        return "error: docker_create requires 'name' and 'image'"
    mgr = get_docker_manager()
    try:
        cid = await mgr.create(
            name,
            image,
            command=arguments.get("command"),
            ports=arguments.get("ports"),
            env=arguments.get("env"),
            volumes=arguments.get("volumes"),
            network=arguments.get("network"),
            memory=arguments.get("memory"),
            cpus=arguments.get("cpus"),
            restart=arguments.get("restart"),
        )
    except DockerError as exc:
        return f"docker create failed: {exc}"
    return f"container '{name}' started (id {cid[:12]})"


async def docker_list(arguments: dict, _ctx: ToolContext) -> str:
    """List containers (running by default)."""
    all_containers = bool(arguments.get("all") or False)
    mgr = get_docker_manager()
    try:
        containers = await mgr.list(all=all_containers)
    except DockerError as exc:
        return f"docker list failed: {exc}"
    if not containers:
        return "no containers"
    lines = [f"{c.name}  [{c.status}]  image={c.image}" + (f"  ports={c.ports}" if c.ports else "")]
    return "\n".join(lines)


async def docker_stop(arguments: dict, _ctx: ToolContext) -> str:
    """Stop a running container."""
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "error: docker_stop requires 'name'"
    mgr = get_docker_manager()
    try:
        await mgr.stop(name)
    except DockerError as exc:
        return f"docker stop failed: {exc}"
    return f"container '{name}' stopped"


async def docker_remove(arguments: dict, _ctx: ToolContext) -> str:
    """Remove a container (force=True kills a running one first)."""
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "error: docker_remove requires 'name'"
    force = bool(arguments.get("force") or False)
    mgr = get_docker_manager()
    try:
        await mgr.remove(name, force=force)
    except DockerError as exc:
        return f"docker remove failed: {exc}"
    return f"container '{name}' removed"


async def docker_logs(arguments: dict, _ctx: ToolContext) -> str:
    """Get the last N lines of a container's logs."""
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "error: docker_logs requires 'name'"
    tail = int(arguments.get("tail") or 100)
    mgr = get_docker_manager()
    try:
        out = await mgr.logs(name, tail=tail)
    except DockerError as exc:
        return f"docker logs failed: {exc}"
    if not out.strip():
        return "(no logs)"
    text = out
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n… [truncated]"
    return text


async def docker_exec(arguments: dict, _ctx: ToolContext) -> str:
    """Run a command inside a running container."""
    name = str(arguments.get("name") or "").strip()
    command = str(arguments.get("command") or "").strip()
    if not name or not command:
        return "error: docker_exec requires 'name' and 'command'"
    mgr = get_docker_manager()
    try:
        rc, out, err = await mgr.exec(name, command)
    except DockerError as exc:
        return f"docker exec failed: {exc}"
    parts = [f"exit code: {rc}"]
    if out:
        parts.append("STDOUT:\n" + out)
    if err:
        parts.append("STDERR:\n" + err)
    if not out and not err:
        parts.append("(no output)")
    text = "\n\n".join(parts)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n… [truncated]"
    return text


def get_tools() -> list[Tool]:
    """The Phase 5 toolset: web search + code interpreter + shell + docker."""
    return [
        Tool(
            name="web_search",
            description=(
                "Search the web (self-hosted SearXNG) for current information: docs, "
                "APIs, libraries, changelogs, versions. Returns the top 6 titles, URLs "
                "and snippets, which cannot be fetched further."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (a few words)."}
                },
                "required": ["query"],
            },
            handler=web_search,
        ),
        Tool(
            name="code_interpreter",
            description=(
                "Run a Python 3 script in a disposable sandbox and get stdout/stderr "
                "back. Use it to verify calculations or logic, transform/sample data, "
                "or sanity-check code before proposing it. Only printed output is "
                "visible — print the results you need (pandas, numpy, matplotlib are "
                "installed; there is NO network access; files are lost after the run)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Complete, self-contained Python script (one-shot, no prior context).",
                    }
                },
                "required": ["code"],
            },
            handler=code_interpreter,
            streams=True,
        ),
        Tool(
            name="shell",
            description=(
                "Run a shell command (bash) in a disposable sandbox and get "
                "stdout/stderr back. Use it for git, package managers, file "
                "operations, or any CLI task. There is NO network access; files "
                "are lost after the run. Only printed output is visible."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Complete shell command (one-shot, no prior context).",
                    }
                },
                "required": ["command"],
            },
            handler=shell,
            streams=True,
        ),
        Tool(
            name="docker_create",
            description=(
                "Create and start a named Docker container. Use it to spin up "
                "dev servers, databases, or any service container. Parameters: "
                "name (required), image (required), command (optional, replaces "
                "default CMD), ports (list of 'host:container' strings), env "
                "(dict), volumes (list of 'host:container' bind mounts), network, "
                "memory, cpus, restart policy."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container name (unique)."},
                    "image": {"type": "string", "description": "Docker image (e.g. 'nginx:alpine')."},
                    "command": {"type": "string", "description": "Optional command to run (replaces default CMD)."},
                    "ports": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Port bindings, e.g. ['8080:80', '5432:5432'].",
                    },
                    "env": {
                        "type": "object",
                        "description": "Environment variables as key-value pairs.",
                    },
                    "volumes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Bind mounts, e.g. ['/host/path:/container/path'].",
                    },
                    "network": {"type": "string", "description": "Docker network name."},
                    "memory": {"type": "string", "description": "Memory limit (e.g. '512m')."},
                    "cpus": {"type": "string", "description": "CPU limit (e.g. '1')."},
                    "restart": {"type": "string", "description": "Restart policy: no, always, on-failure."},
                },
                "required": ["name", "image"],
            },
            handler=docker_create,
        ),
        Tool(
            name="docker_list",
            description="List Docker containers (running by default; set all=true to include exited).",
            parameters={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "description": "Include exited containers."},
                },
                "required": [],
            },
            handler=docker_list,
        ),
        Tool(
            name="docker_stop",
            description="Stop a running Docker container (graceful SIGTERM, then SIGKILL).",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container name or ID."},
                },
                "required": ["name"],
            },
            handler=docker_stop,
        ),
        Tool(
            name="docker_remove",
            description="Remove a Docker container (set force=true to kill a running one first).",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container name or ID."},
                    "force": {"type": "boolean", "description": "Force-remove (kill if running)."},
                },
                "required": ["name"],
            },
            handler=docker_remove,
        ),
        Tool(
            name="docker_logs",
            description="Get the last N lines of a container's logs (stdout+stderr).",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container name or ID."},
                    "tail": {"type": "integer", "description": "Number of lines (default 100, max 200)."},
                },
                "required": ["name"],
            },
            handler=docker_logs,
        ),
        Tool(
            name="docker_exec",
            description="Run a command inside a running container (like `docker exec`).",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container name or ID."},
                    "command": {"type": "string", "description": "Shell command to run inside the container."},
                },
                "required": ["name", "command"],
            },
            handler=docker_exec,
        ),
    ]


def parse_tool_arguments(raw: str) -> dict:
    """Model-provided arguments arrive as a (streamed) JSON string."""
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"_parse_error": raw[:200]}
    return value if isinstance(value, dict) else {"value": value}

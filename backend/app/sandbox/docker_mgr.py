"""Docker container lifecycle manager (create, list, stop, remove, logs, exec).

Distinct from `SandboxManager` (ephemeral, hardened, one-shot code execution)
and `TerminalManager` (persistent interactive shell). This manager lets the
model spin up and manage long-lived containers for the user's benefit —
dev servers, databases, service containers, etc.

All operations go through the Docker CLI (same socket the backend already
has access to). No hardening flags here — these are user-facing containers,
not sandboxed code execution.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from ..core.settings import get_settings

#: Max log lines to return per call (prevents flooding the model context).
MAX_LOG_LINES = 200
#: Timeout for one-shot docker CLI operations (seconds).
EXEC_TIMEOUT = 60.0
#: Timeout for `docker run` (container start) — longer for image pulls.
RUN_TIMEOUT = 300.0


class DockerError(RuntimeError):
    """Raised when a docker CLI operation fails."""


@dataclass
class ContainerInfo:
    """Summary of a running/exited container (from `docker ps --format json`)."""

    id: str
    name: str
    image: str
    status: str
    ports: str
    created: str


@dataclass
class DockerManager:
    """Drives the Docker CLI for container lifecycle management."""

    docker_cli: str = "docker"

    # ------------------------------------------------------------------ #
    # core CLI seam
    # ------------------------------------------------------------------ #
    async def _exec(
        self,
        args: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout: float = EXEC_TIMEOUT,
    ) -> tuple[int, str, str]:
        """One-shot CLI op. Returns (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            self.docker_cli,
            *args,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(input=input_bytes), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise DockerError(f"`docker {' '.join(args[:2])}` timed out after {timeout:.0f}s") from None
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    # ------------------------------------------------------------------ #
    # container lifecycle
    # ------------------------------------------------------------------ #
    async def create(
        self,
        name: str,
        image: str,
        *,
        command: str | None = None,
        ports: list[str] | None = None,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        network: str | None = None,
        memory: str | None = None,
        cpus: str | None = None,
        restart: str | None = None,
    ) -> str:
        """Create and start a named container. Returns the container ID.

        - `ports`: list of "host:container" strings (e.g. ["8080:80"])
        - `env`: dict of environment variables
        - `volumes`: list of "host:container" bind mounts
        - `network`: docker network name (default: bridge)
        - `restart`: restart policy ("no", "always", "on-failure")
        """
        args = ["run", "-d", "--name", name]

        if command:
            # `command` replaces the image's default CMD/ENTRYPOINT
            pass  # appended after image below

        if ports:
            for p in ports:
                args += ["-p", p]
        if env:
            for k, v in env.items():
                args += ["-e", f"{k}={v}"]
        if volumes:
            for v in volumes:
                args += ["-v", v]
        if network:
            args += ["--network", network]
        if memory:
            args += ["--memory", memory, "--memory-swap", memory]
        if cpus:
            args += ["--cpus", cpus]
        if restart:
            args += ["--restart", restart]

        args.append(image)
        if command:
            args += ["sh", "-c", command]

        rc, out, err = await self._exec(args, timeout=RUN_TIMEOUT)
        if rc != 0:
            tail = (err or out).strip().splitlines()[-5:]
            raise DockerError(f"docker run failed (exit {rc}): {' | '.join(tail)}")
        return out.strip()

    async def list(self, *, all: bool = False) -> list[ContainerInfo]:
        """List containers (running by default; `all=True` includes exited)."""
        args = ["ps", "--format", "json"]
        if all:
            args.append("--all")
        rc, out, _ = await self._exec(args, timeout=30)
        if rc != 0:
            return []
        containers: list[ContainerInfo] = []
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                containers.append(
                    ContainerInfo(
                        id=str(row.get("ID") or "")[:12],
                        name=str(row.get("Names") or ""),
                        image=str(row.get("Image") or ""),
                        status=str(row.get("Status") or ""),
                        ports=str(row.get("Ports") or ""),
                        created=str(row.get("Created") or ""),
                    )
                )
            except (json.JSONDecodeError, TypeError):
                continue
        return containers

    async def stop(self, name_or_id: str, *, timeout: int = 10) -> None:
        """Stop a running container (SIGTERM, then SIGKILL after timeout)."""
        rc, out, err = await self._exec(
            ["stop", "-t", str(timeout), name_or_id], timeout=timeout + 15
        )
        if rc != 0:
            tail = (err or out).strip().splitlines()[-3:]
            raise DockerError(f"docker stop failed (exit {rc}): {' | '.join(tail)}")

    async def remove(self, name_or_id: str, *, force: bool = False) -> None:
        """Remove a container (stopped by default; `force=True` kills first)."""
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(name_or_id)
        rc, out, err = await self._exec(args, timeout=30)
        if rc != 0:
            tail = (err or out).strip().splitlines()[-3:]
            raise DockerError(f"docker rm failed (exit {rc}): {' | '.join(tail)}")

    async def logs(self, name_or_id: str, *, tail: int = MAX_LOG_LINES) -> str:
        """Get the last `tail` lines of container logs (stdout+stderr)."""
        rc, out, err = await self._exec(
            ["logs", "--tail", str(tail), name_or_id], timeout=30
        )
        if rc != 0:
            tail_err = (err or out).strip().splitlines()[-3:]
            raise DockerError(f"docker logs failed (exit {rc}): {' | '.join(tail_err)}")
        return out

    async def exec(
        self,
        name_or_id: str,
        command: str,
        *,
        timeout: float = EXEC_TIMEOUT,
    ) -> tuple[int, str, str]:
        """Run a command inside a running container. Returns (rc, stdout, stderr)."""
        rc, out, err = await self._exec(
            ["exec", name_or_id, "sh", "-c", command], timeout=timeout
        )
        return rc, out, err

    async def inspect(self, name_or_id: str) -> dict:
        """Get container metadata (state, config, mounts, network)."""
        rc, out, err = await self._exec(
            ["inspect", name_or_id], timeout=30
        )
        if rc != 0:
            tail = (err or out).strip().splitlines()[-3:]
            raise DockerError(f"docker inspect failed (exit {rc}): {' | '.join(tail)}")
        data = json.loads(out)
        # `docker inspect` returns a list with one element
        return data[0] if isinstance(data, list) and data else {}


# --------------------------------------------------------------------------- #
# process-wide singleton
# --------------------------------------------------------------------------- #
_manager: DockerManager | None = None


def get_docker_manager() -> DockerManager:
    """Process-wide DockerManager; tests may monkey-patch this."""
    global _manager
    if _manager is None:
        _manager = DockerManager()
    return _manager


def reset_docker_manager() -> None:
    """Drop the cached manager (test seam)."""
    global _manager
    _manager = None

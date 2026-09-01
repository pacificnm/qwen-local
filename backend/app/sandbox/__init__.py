"""Docker sandboxes: the ephemeral code interpreter, the interactive terminal,
and the container lifecycle manager (create / list / stop / remove / logs / exec)."""

from .docker_mgr import (
    ContainerInfo,
    DockerError,
    DockerManager,
    get_docker_manager,
    reset_docker_manager,
)
from .manager import RunResult, SandboxError, SandboxManager
from .terminal import (
    LiveProc,
    TerminalError,
    TerminalManager,
    TerminalSession,
    encode_input,
    encode_resize,
    get_terminal_manager,
    resolve_project_container,
    set_terminal_manager,
)

__all__ = [
    "ContainerInfo",
    "DockerError",
    "DockerManager",
    "RunResult",
    "SandboxError",
    "SandboxManager",
    "TerminalError",
    "TerminalManager",
    "TerminalSession",
    "LiveProc",
    "encode_input",
    "encode_resize",
    "get_docker_manager",
    "get_terminal_manager",
    "reset_docker_manager",
    "resolve_project_container",
    "set_terminal_manager",
]

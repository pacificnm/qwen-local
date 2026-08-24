"""Ephemeral Docker sandbox (spec §4.5) — `code_interpreter` runtime."""

from .manager import RunResult, SandboxError, SandboxManager

__all__ = ["RunResult", "SandboxError", "SandboxManager"]

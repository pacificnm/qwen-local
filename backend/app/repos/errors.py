"""Shared error types for the ingestion pipeline."""


class SyncError(Exception):
    """Ingestion cannot continue; text is user-safe (secrets scrubbed by caller)."""


class GitError(SyncError):
    """Git subprocess failure (message already scrubbed of credentials)."""


class FileNotFound(SyncError):
    """The requested file does not exist in the repository (→ 422)."""


class InvalidBranch(SyncError):
    """Branch name is not a valid git ref (→ 422)."""


class GithubApiError(SyncError):
    """GitHub REST API rejected the request or was unreachable (→ 502)."""

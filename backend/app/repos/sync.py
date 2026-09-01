"""Repo ingestion job: clone → blob-sha diff → chunk → embed → pgvector.

Single in-process registry (the backend runs one worker, one user). Jobs run
as asyncio tasks so the API can report progress; unlink cancels the task.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select

from app.core.settings import get_settings
from app.db.models import CodeChunk, CodeFile, Repository
from app.db.session import get_session_factory

from . import gitops
from .chunking import MAX_FILE_BYTES, chunk_source, language_for
from .embeddings import embed_texts
from .errors import SyncError


def workspace() -> Path:
    settings = get_settings()
    p = Path(settings.workspace_dir)
    if p.is_absolute():
        return p
    # Relative default resolves against the project root (this file lives in
    # backend/app/repos/), matching the compose bind-mount source. In the
    # container that hierarchy is flatter, so compose pins it with the
    # absolute WORKSPACE_DIR instead of relying on this fallback.
    root = Path(__file__).resolve().parents[3]
    return root / p


def resolve_repo_host_dir(full_name: str) -> str | None:
    """Host-side path of the repo worktree, for bind-mounting into a sandbox.

    The docker daemon (on the host) resolves bind sources against HOST paths,
    so this must return a path as seen FROM THE HOST, not the backend
    container.

    - ``workspace_host_dir`` (set in compose) is authoritative: it is the host
      directory behind the container's workspace mount (e.g.
      ``/data/.../workspace`` behind ``/srv/app/workspace``). That host path is
      *not* present inside the backend container by construction, so it is
      returned as-is with NO existence check — a container-namespace
      ``is_dir()`` would always miss and silently fall through to the
      in-container path below, which the host daemon cannot see (→ an empty
      bind mount).
    - ``workspace() / slug`` is the container view of the SAME clone (the
      ``workspace`` dir is the bind-mount target). It IS present in-container
      for a cloned repo, so ``.is_dir()`` on *it* is a valid "is it cloned?"
      guard in both compose and host-local contexts.
    - ``workspace_host_dir`` unset (host-local dev, backend and daemon share a
      filesystem) → the in-container path IS a valid host path and is used
      directly.

    Net: a cloned repo → the host bind source (real files); an uncloned repo
    → ``None`` so the caller falls back to a writable scratch dir instead.
    """
    settings = get_settings()
    slug = full_name.replace("/", "__")
    clone_in_container = gitops.workspace_repo_dir(workspace(), full_name)
    if settings.workspace_host_dir:
        if clone_in_container.is_dir():
            return str(Path(settings.workspace_host_dir) / slug)
        return None
    if clone_in_container.is_dir():
        return str(clone_in_container)
    return None


def _iso(t: float | None) -> str | None:
    return datetime.fromtimestamp(t, UTC).isoformat() if t is not None else None


@dataclass
class SyncJob:
    repo_id: uuid.UUID
    stage: str = "queued"  # queued | cloning | scanning | processing | done | error
    files_total: int = 0
    files_done: int = 0
    files_added: int = 0
    files_removed: int = 0
    chunks_written: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None

    def is_running(self) -> bool:
        return self.stage not in ("done", "error")

    def public(self) -> dict:
        return {
            "repo_id": str(self.repo_id),
            "state": "running" if self.is_running() else self.stage,
            "stage": self.stage,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "files_added": self.files_added,
            "files_removed": self.files_removed,
            "chunks_written": self.chunks_written,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "error": self.error,
        }


_jobs: dict[uuid.UUID, SyncJob] = {}
_tasks: dict[uuid.UUID, asyncio.Task] = {}


def _plan_to_process(
    new_map: dict[str, str], old_map: dict[str, str], chunked: set[str]
) -> list[str]:
    """Paths to (re)process: content changed, OR a stale file row that has no
    chunks (files skipped by an earlier, narrower ingestion pass — e.g. docs)."""
    return sorted(
        p for p, sha in new_map.items() if old_map.get(p) != sha or p not in chunked
    )


def get_job(repo_id: uuid.UUID) -> SyncJob | None:
    return _jobs.get(repo_id)


def submit(repo_id: uuid.UUID) -> SyncJob:
    existing = _jobs.get(repo_id)
    if existing is not None and existing.is_running():
        raise SyncError("A sync for this repository is already running")
    job = SyncJob(repo_id=repo_id)
    _jobs[repo_id] = job
    _tasks[repo_id] = asyncio.create_task(_run(job), name=f"repo-sync-{repo_id}")
    return job


def cancel(repo_id: uuid.UUID) -> None:
    task = _tasks.pop(repo_id, None)
    if task is not None:
        task.cancel()


async def _run(job: SyncJob) -> None:
    settings = get_settings()
    pat = settings.github_pat

    def scrub(s: str) -> str:
        return s.replace(pat, "[redacted]") if pat else s

    try:
        # 1) Repo row (single shared user; repo access is global).
        factory = get_session_factory()
        async with factory() as db:
            repo = await db.get(Repository, job.repo_id)
            if repo is None:
                raise SyncError("Repository no longer exists")
            full_name = repo.github_full_name

        # 2) Clone / refresh.
        job.stage = "cloning"
        repo_dir, branch = await gitops.ensure_repo(workspace(), full_name, pat)

        # 3) Scan the tree and diff against the last sync (by blob sha).
        job.stage = "scanning"
        try:
            head = await gitops.head_sha(repo_dir)
        except gitops.GitError:
            head = None  # unborn HEAD: zero commits — nothing to ingest yet
        if head is None:
            # Fresh empty repo: finish cleanly (no scary error state) so the
            # repo stays usable — the first commit is pushed from the Git tab,
            # and the next sync picks it up.
            async with factory() as db:
                repo = await db.get(Repository, job.repo_id)
                if repo is not None:
                    repo.last_synced_at = datetime.now(UTC)
                    if branch != "HEAD":
                        repo.default_branch = branch
                await db.commit()
            job.stage = "done"
            return
        tree = await gitops.list_tree(repo_dir)
        new_map = {f.path: f.blob_sha for f in tree}

        async with factory() as db:
            old_rows = (
                await db.execute(
                    select(CodeFile.file_path, CodeFile.git_blob_sha).where(CodeFile.repo_id == job.repo_id)
                )
            ).all()
            chunked_rows = (
                await db.execute(
                    select(CodeChunk.file_path).where(CodeChunk.repo_id == job.repo_id)
                )
            ).all()
        old_map = dict(old_rows)
        chunked = {r[0] for r in chunked_rows}
        to_process = _plan_to_process(new_map, old_map, chunked)
        removed = sorted(p for p in old_map if p not in new_map)
        job.files_total = len(to_process)
        job.files_added = sum(1 for p in to_process if p not in old_map)
        job.files_removed = len(removed)

        # 4) Chunk + embed changed files; skip rules decide what gets vectorized.
        job.stage = "processing"
        for idx, path in enumerate(to_process, 1):
            blob_sha = new_map[path]
            lang = language_for(path)
            file_path = repo_dir / path
            chunks = []
            if lang is not None and file_path.is_file() and file_path.stat().st_size <= MAX_FILE_BYTES:
                text = file_path.read_text(encoding="utf-8", errors="replace")
                chunks = chunk_source(text, lang)

            vectors = await embed_texts([c.text for c in chunks]) if chunks else []

            async with factory() as db:
                await db.execute(
                    delete(CodeChunk).where(CodeChunk.repo_id == job.repo_id, CodeChunk.file_path == path)
                )
                for chunk, vec in zip(chunks, vectors, strict=True):
                    db.add(
                        CodeChunk(
                            repo_id=job.repo_id,
                            file_path=path,
                            language=lang or "unknown",
                            start_line=chunk.start_line,
                            end_line=chunk.end_line,
                            content=chunk.text,
                            token_estimate=chunk.token_estimate,
                            embedding=vec,
                        )
                    )
                row = await db.scalar(
                    select(CodeFile).where(CodeFile.repo_id == job.repo_id, CodeFile.file_path == path)
                )
                if row is None:
                    db.add(CodeFile(repo_id=job.repo_id, file_path=path, git_blob_sha=blob_sha))
                else:
                    row.git_blob_sha = blob_sha
                    row.synced_at = datetime.now(UTC)
                await db.commit()

            job.files_done = idx
            job.chunks_written += len(chunks)

        # 5) Drop deleted files + their chunks; stamp the repo.
        async with factory() as db:
            if removed:
                await db.execute(
                    delete(CodeChunk).where(
                        CodeChunk.repo_id == job.repo_id, CodeChunk.file_path.in_(removed)
                    )
                )
                await db.execute(
                    delete(CodeFile).where(CodeFile.repo_id == job.repo_id, CodeFile.file_path.in_(removed))
                )
            repo = await db.get(Repository, job.repo_id)
            if repo is not None:
                repo.last_commit_sha = head
                repo.last_synced_at = datetime.now(UTC)
                if branch != "HEAD":
                    repo.default_branch = branch
            await db.commit()

        job.stage = "done"
    except asyncio.CancelledError:
        job.stage = "error"
        job.error = "cancelled"
    except SyncError as exc:
        job.stage = "error"
        job.error = scrub(str(exc))
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the worker
        job.stage = "error"
        job.error = scrub(f"unexpected: {exc}")
    finally:
        job.finished_at = job.finished_at or time.time()
        _tasks.pop(job.repo_id, None)
        # Job stays in the registry for status lookups (single user, bounded by repo count).

"""Unit tests for context-menu file mutations: rename_entry / delete_entry /
create_file on the local worktree. No network, no git state needed — these
operate on plain files inside a repo-like tmp dir.
"""

from pathlib import Path

import pytest

from app.repos import gitops
from app.repos.errors import FileExists, FileNotFound, GitError


def _repo(tmp_path: Path, **files: str) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# --- rename_entry -------------------------------------------------------------


def test_rename_file(tmp_path):
    _repo(tmp_path, **{"a/old.txt": "v1"})
    gitops.rename_entry(tmp_path, "a/old.txt", "a/new.txt")
    assert not (tmp_path / "a/old.txt").exists()
    assert (tmp_path / "a/new.txt").read_text(encoding="utf-8") == "v1"


def test_rename_folder(tmp_path):
    _repo(tmp_path, **{"d/x.txt": "x", "d/y.txt": "y"})
    gitops.rename_entry(tmp_path, "d", "renamed")
    assert (tmp_path / "renamed/x.txt").exists() and (tmp_path / "renamed/y.txt").exists()
    assert not (tmp_path / "d").exists()


def test_rename_missing_source_raises(tmp_path):
    _repo(tmp_path, **{"keep.txt": "k"})
    with pytest.raises(FileNotFound):
        gitops.rename_entry(tmp_path, "ghost.txt", "other.txt")


def test_rename_existing_destination_raises(tmp_path):
    _repo(tmp_path, **{"one.txt": "1", "two.txt": "2"})
    with pytest.raises(FileExists):
        gitops.rename_entry(tmp_path, "one.txt", "two.txt")
    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "1"


def test_rename_into_missing_folder_raises(tmp_path):
    _repo(tmp_path, **{"one.txt": "1"})
    with pytest.raises(GitError):
        gitops.rename_entry(tmp_path, "one.txt", "nope/one.txt")


def test_rename_rejects_path_escape(tmp_path):
    _repo(tmp_path, **{"one.txt": "1"})
    with pytest.raises(GitError):
        gitops.rename_entry(tmp_path, "one.txt", "../outside.txt")
    with pytest.raises(GitError):
        gitops.rename_entry(tmp_path, "../outside-in.txt", "one.txt")


# --- delete_entry -------------------------------------------------------------


def test_delete_file(tmp_path):
    _repo(tmp_path, **{"gone.txt": "x", "stay.txt": "y"})
    gitops.delete_entry(tmp_path, "gone.txt")
    assert not (tmp_path / "gone.txt").exists()
    assert (tmp_path / "stay.txt").exists()


def test_delete_folder_recursive(tmp_path):
    _repo(tmp_path, **{"tree/a/b/deep.txt": "d", "tree/keep.txt": "k", "outside.txt": "o"})
    gitops.delete_entry(tmp_path, "tree")
    assert not (tmp_path / "tree").exists()
    assert (tmp_path / "outside.txt").exists()


def test_delete_missing_raises(tmp_path):
    _repo(tmp_path, **{"keep.txt": "k"})
    with pytest.raises(FileNotFound):
        gitops.delete_entry(tmp_path, "ghost.txt")


def test_delete_rejects_root_and_escape(tmp_path):
    _repo(tmp_path, **{"keep.txt": "k"})
    with pytest.raises(GitError):
        gitops.delete_entry(tmp_path, ".")
    with pytest.raises(GitError):
        gitops.delete_entry(tmp_path, "../sibling")


# --- create_file ---------------------------------------------------------------


def test_create_file_makes_parents(tmp_path):
    _repo(tmp_path, **{"keep.txt": "k"})
    gitops.create_file(tmp_path, "src/new/dir/created.txt", "hello")
    assert (tmp_path / "src/new/dir/created.txt").read_text(encoding="utf-8") == "hello"


def test_create_existing_raises(tmp_path):
    _repo(tmp_path, **{"exists.txt": "v"})
    with pytest.raises(FileExists):
        gitops.create_file(tmp_path, "exists.txt", "v2")
    assert (tmp_path / "exists.txt").read_text(encoding="utf-8") == "v"


def test_create_rejects_path_escape(tmp_path):
    with pytest.raises(GitError):
        gitops.create_file(tmp_path, "../sibling.txt", "x")
    with pytest.raises(GitError):
        gitops.create_file(tmp_path, "/abs.txt", "x")


# --- create_folder -------------------------------------------------------------


def test_create_folder_plants_gitkeep(tmp_path):
    _repo(tmp_path, **{"keep.txt": "k"})
    gitops.create_folder(tmp_path, "docs")
    assert (tmp_path / "docs").is_dir()
    keep = tmp_path / "docs" / ".gitkeep"
    assert keep.is_file()
    assert keep.read_text(encoding="utf-8") == ""


def test_create_folder_nested_makes_parents(tmp_path):
    gitops.create_folder(tmp_path, "a/b/c")
    assert (tmp_path / "a/b/c").is_dir()
    assert (tmp_path / "a/b/c/.gitkeep").is_file()


def test_create_folder_existing_folder_raises(tmp_path):
    _repo(tmp_path, **{"has/inner.txt": "x"})
    with pytest.raises(FileExists):
        gitops.create_folder(tmp_path, "has")
    assert (tmp_path / "has/inner.txt").read_text(encoding="utf-8") == "x"


def test_create_folder_over_file_raises(tmp_path):
    _repo(tmp_path, **{"blocker.txt": "v"})
    with pytest.raises(FileExists):
        gitops.create_folder(tmp_path, "blocker.txt")
    assert (tmp_path / "blocker.txt").read_text(encoding="utf-8") == "v"


def test_create_folder_rejects_root_and_escape(tmp_path):
    with pytest.raises(GitError):
        gitops.create_folder(tmp_path, ".")
    with pytest.raises(GitError):
        gitops.create_folder(tmp_path, "../sibling")

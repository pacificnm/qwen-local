"""Sync ingestion plan: which files get (re)chunked + embedded."""

from app.repos.sync import _plan_to_process


def test_unchanged_files_with_chunks_are_skipped():
    new = {"a.py": "s1", "b.md": "s2"}
    old = {"a.py": "s1", "b.md": "s2"}
    assert _plan_to_process(new, old, {"a.py", "b.md"}) == []


def test_stale_file_row_without_chunks_is_reprocessed():
    # Docs files were recorded by an earlier, narrower ingestion pass but
    # never embedded — they must be picked up again even though the blob sha
    # is unchanged.
    new = {"README.md": "s1", "main.py": "s2"}
    old = {"README.md": "s1", "main.py": "s2"}
    assert _plan_to_process(new, old, {"main.py"}) == ["README.md"]


def test_changed_and_new_files_are_processed():
    new = {"a.py": "s2", "c.md": "s3"}
    old = {"a.py": "s1"}
    assert _plan_to_process(new, old, {"a.py"}) == ["a.py", "c.md"]

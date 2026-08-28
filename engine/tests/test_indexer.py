"""The index must track the repository, and a re-index after one commit must be cheap."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from reviewrig.context.chunker import chunk_file
from reviewrig.context.indexer import changed_files, reindex
from reviewrig.store import Store
from reviewrig.store.index import chunk_count, chunks_for_symbol, chunks_in_file, search_text
from tests.helpers import git_commit, git_init

READER = "def read(path):\n    return path\n"
WRITER = "def write(path, body):\n    read(path)\n    return body\n"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = git_init(tmp_path / "repo")
    git_commit(path, {"reader.py": READER, "writer.py": WRITER}, "start")
    return path


async def test_it_indexes_every_tracked_file(store: Store, repository: Path) -> None:
    outcome = await reindex(store, None, repository)
    assert outcome.files_seen == 2
    assert outcome.files_changed == 2
    assert chunk_count(store, repository) >= 2


async def test_a_second_pass_reads_nothing(store: Store, repository: Path) -> None:
    """This is what makes a continuous rig affordable."""
    await reindex(store, None, repository)
    outcome = await reindex(store, None, repository)
    assert outcome.files_changed == 0
    assert outcome.chunks_written == 0


async def test_only_the_changed_file_is_re_read(store: Store, repository: Path) -> None:
    await reindex(store, None, repository)
    git_commit(repository, {"reader.py": READER + "\n\ndef extra():\n    return 1\n"}, "extra")
    outcome = await reindex(store, None, repository)
    assert outcome.files_changed == 1


async def test_a_removed_file_leaves_the_index(store: Store, repository: Path) -> None:
    await reindex(store, None, repository)
    (repository / "writer.py").unlink()
    git_commit(repository, {}, "drop the writer")
    outcome = await reindex(store, None, repository)
    assert outcome.files_removed == 1
    assert chunks_in_file(store, repository, "writer.py") == []


async def test_a_changed_symbol_is_findable_by_name(store: Store, repository: Path) -> None:
    await reindex(store, None, repository)
    hits = chunks_for_symbol(store, str(repository), "read")
    assert hits
    assert hits[0].path == "reader.py"


async def test_a_caller_is_findable_by_keyword(store: Store, repository: Path) -> None:
    """This is how the rig finds who calls a changed function, in any language."""
    await reindex(store, None, repository)
    hits = search_text(store, "read", str(repository), limit=10)
    assert "writer.py" in {hit.path for hit in hits}


async def test_a_lock_file_is_recorded_but_not_chunked(store: Store, repository: Path) -> None:
    """It must not be re-read every cycle either."""
    git_commit(repository, {"pnpm-lock.yaml": "lockfileVersion: 9\n" * 100}, "add a lock file")
    await reindex(store, None, repository)
    assert chunks_in_file(store, repository, "pnpm-lock.yaml") == []
    outcome = await reindex(store, None, repository)
    assert outcome.files_changed == 0


async def test_a_binary_file_does_not_stop_the_index(store: Store, repository: Path) -> None:
    (repository / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 100)
    git_commit(repository, {}, "add a binary file")
    outcome = await reindex(store, None, repository)
    assert outcome.error is None
    assert chunk_count(store, repository) >= 2


async def test_a_path_that_is_not_a_repository_reports_the_reason(
    store: Store, tmp_path: Path
) -> None:
    outcome = await reindex(store, None, tmp_path / "gone")
    assert outcome.error


def test_changed_files_names_what_moved(store: Store, repository: Path) -> None:
    current, changed, removed = changed_files(store, repository)
    assert sorted(current) == ["reader.py", "writer.py"]
    assert sorted(changed) == ["reader.py", "writer.py"]
    assert removed == []


def test_a_chunk_follows_a_symbol_boundary() -> None:
    chunks = chunk_file("a.py", "def one():\n    return 1\n\n\ndef two():\n    return 2\n")
    symbols = [chunk.symbol for chunk in chunks if chunk.symbol]
    assert symbols == ["one", "two"]


def test_a_long_symbol_is_split_with_an_overlap() -> None:
    body = "\n".join(f"    x = {index}" for index in range(400))
    chunks = chunk_file("a.py", f"def big():\n{body}\n")
    assert len(chunks) > 1
    assert all("part" in chunk.symbol for chunk in chunks)
    assert chunks[1].start_line < chunks[0].end_line


def test_a_file_with_no_grammar_is_split_into_windows() -> None:
    chunks = chunk_file("notes.txt", "line\n" * 300)
    assert len(chunks) > 1
    assert all(chunk.kind == "lines" for chunk in chunks)


def test_an_empty_file_makes_no_chunk() -> None:
    assert chunk_file("a.py", "   \n\n") == []

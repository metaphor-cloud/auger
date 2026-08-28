from __future__ import annotations

from pathlib import Path

from reviewrig.models import Remote, Repository
from reviewrig.store import Store
from reviewrig.store.db import MIGRATIONS
from reviewrig.store.repositories import list_repositories, record_scan


def repository(path: str, name: str = "thing") -> Repository:
    return Repository(path=Path(path), remote=Remote("github.com", "acme", name))


def test_a_new_database_is_at_the_latest_version(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    assert store.version == len(MIGRATIONS)
    store.close()


def test_reopening_does_not_reapply_a_migration(tmp_path: Path) -> None:
    Store.open(tmp_path).close()
    store = Store.open(tmp_path)
    assert store.version == len(MIGRATIONS)
    store.close()


def test_a_scan_stores_what_it_found(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a"), repository("/x/b", "other")])
    assert [found.path for found in list_repositories(store)] == [Path("/x/a"), Path("/x/b")]
    assert list_repositories(store)[1].remote == Remote("github.com", "acme", "other")
    store.close()


def test_a_repository_that_disappears_keeps_its_row(tmp_path: Path) -> None:
    """A temporary unmount must not throw away the findings for a repository."""
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a"), repository("/x/b")], timestamp="t1")
    record_scan(store, [repository("/x/a")], timestamp="t2")
    assert [found.path for found in list_repositories(store)] == [Path("/x/a")]
    assert len(list_repositories(store, present_only=False)) == 2
    store.close()


def test_a_repository_that_returns_becomes_present_again(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a")], timestamp="t1")
    record_scan(store, [], timestamp="t2")
    assert list_repositories(store) == []
    record_scan(store, [repository("/x/a")], timestamp="t3")
    assert [found.path for found in list_repositories(store)] == [Path("/x/a")]
    store.close()


def test_a_scan_keeps_the_first_sighting(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a")], timestamp="t1")
    record_scan(store, [repository("/x/a")], timestamp="t2")
    row = store.query("SELECT first_seen_at, last_seen_at FROM repositories")[0]
    assert row["first_seen_at"] == "t1"
    assert row["last_seen_at"] == "t2"
    store.close()


def test_a_repository_with_no_remote_stores_null(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    record_scan(store, [Repository(path=Path("/x/a"))])
    assert list_repositories(store)[0].remote is None
    store.close()


def test_a_failed_write_rolls_back(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a")])
    try:
        with store.write() as connection:
            connection.execute("DELETE FROM repositories")
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    assert len(list_repositories(store)) == 1
    store.close()


def test_two_scans_inside_one_second_still_mark_absence(tmp_path: Path) -> None:
    """A timestamp comparison would fail here, because both scans share a timestamp."""
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a"), repository("/x/b")], timestamp="t1")
    record_scan(store, [repository("/x/a")], timestamp="t1")
    assert [found.path for found in list_repositories(store)] == [Path("/x/a")]
    store.close()


def test_an_empty_scan_marks_everything_absent(tmp_path: Path) -> None:
    store = Store.open(tmp_path)
    record_scan(store, [repository("/x/a")], timestamp="t1")
    record_scan(store, [], timestamp="t1")
    assert list_repositories(store) == []
    assert len(list_repositories(store, present_only=False)) == 1
    store.close()

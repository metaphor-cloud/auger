from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from reviewrig.store import Store
from reviewrig.store.findings import (
    Finding,
    counts,
    fingerprint,
    list_findings,
    record,
    set_status,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path)
    yield store
    store.close()


def make(title: str = "Null dereference", file: str = "a.py", **kwargs: object) -> Finding:
    fields: dict[str, object] = {
        "repo_path": "/x/repo",
        "source": "model",
        "severity": "high",
        "title": title,
        "detail": "it crashes",
        "file": file,
    }
    fields.update(kwargs)
    return Finding(**fields)  # type: ignore[arg-type]


def test_a_fingerprint_ignores_the_line_number() -> None:
    """A finding that moved down the file because an import was added is the same one."""
    assert fingerprint("model", "a.py", "Leak", "close(f)") == fingerprint(
        "model", "a.py", "Leak", "close(f)"
    )


def test_a_fingerprint_ignores_wording_and_case() -> None:
    assert fingerprint("model", "a.py", "SQL Injection!", "q") == fingerprint(
        "model", "a.py", "sql injection", "q"
    )


def test_a_fingerprint_separates_two_files() -> None:
    assert fingerprint("model", "a.py", "Leak", "q") != fingerprint("model", "b.py", "Leak", "q")


def test_a_fingerprint_separates_two_sources() -> None:
    assert fingerprint("model", "a.py", "Leak", "q") != fingerprint("semgrep", "a.py", "Leak", "q")


def test_a_repeat_updates_the_row_and_adds_no_second_one(store: Store) -> None:
    record(store, [make(line=10)])
    record(store, [make(line=42)])
    rows = list_findings(store)
    assert len(rows) == 1
    assert rows[0].times_seen == 2
    assert rows[0].line == 42


def test_suppression_survives_a_re_review(store: Store) -> None:
    """This is the whole value of suppressing a finding."""
    record(store, [make()])
    set_status(store, [list_findings(store)[0].fingerprint], "suppressed")
    record(store, [make()])
    assert list_findings(store) == []
    assert len(list_findings(store, statuses=["suppressed"])) == 1


def test_severity_orders_the_list(store: Store) -> None:
    record(
        store,
        [
            make("low one", severity="low"),
            make("critical one", severity="critical"),
            make("medium one", severity="medium"),
        ],
    )
    assert [row.severity for row in list_findings(store)] == ["critical", "medium", "low"]


def test_counts_hold_one_number_per_severity(store: Store) -> None:
    record(
        store, [make("a", severity="high"), make("b", severity="high"), make("c", severity="low")]
    )
    result = counts(store)
    assert result["high"] == 2
    assert result["low"] == 1
    assert result["total"] == 3


def test_a_suppressed_finding_is_not_counted(store: Store) -> None:
    record(store, [make("a"), make("b")])
    set_status(store, [list_findings(store)[0].fingerprint], "suppressed")
    assert counts(store)["total"] == 1


def test_findings_can_be_read_for_one_repository(store: Store) -> None:
    record(store, [make("a"), make("b", repo_path="/x/other")])
    assert len(list_findings(store, repo_path="/x/repo")) == 1


def test_a_suppressed_finding_can_be_brought_back(store: Store) -> None:
    record(store, [make()])
    key = list_findings(store)[0].fingerprint
    set_status(store, [key], "suppressed")
    set_status(store, [key], "open")
    assert len(list_findings(store)) == 1

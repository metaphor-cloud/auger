"""The narrow, in-process tools.

The point of these is that a turn is cheap. A loop over a tool that costs seconds is
not a slow loop, it is a broken one, so the speed is asserted here rather than assumed.
Everything else under test is what keeps a result bounded, because a tool result is
carried in every request after the one that asked for it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from auger.config.schema import CodeGraph as CodeGraphConfig
from auger.context import reindex
from auger.jobs.lookup import (
    CALLERS,
    READ,
    READ_LINES,
    RESULT_CHARS,
    SEARCH,
    SYMBOLS,
    Lookup,
)
from auger.store import Store
from tests.helpers import git_commit, git_init

SOURCE = '''\
"""A module with something to find."""


def collect(rows):
    """Add up the rows."""
    total = 0
    for row in rows:
        total += row
    return total


class Ledger:
    def post(self, amount):
        return collect([amount])
'''


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = git_init(tmp_path / "repo")
    git_commit(path, {"src/ledger.py": SOURCE, "README.md": "# thing\n"}, "start")
    return path


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
async def lookup(store: Store, repository: Path) -> Lookup:
    await reindex(store, None, repository)
    return Lookup(store, repository)


# --- the speed that makes a loop viable ----------------------------------------------


def test_every_tool_answers_in_milliseconds(lookup: Lookup) -> None:
    """A loop is only viable when a turn is cheap. A container start is seconds; these
    read an index that is already built, so twenty turns cost less than one model call.

    The ceiling is generous on purpose. It is not a benchmark, it is a guard against a
    tool quietly growing a subprocess, a network call, or a walk of the whole tree.
    """
    calls = [
        (READ, {"path": "src/ledger.py"}),
        (SEARCH, {"query": "collect"}),
        (SYMBOLS, {"path": "src/ledger.py"}),
        (CALLERS, {"symbol": "collect"}),
    ]
    for name, arguments in calls:
        started = time.monotonic()
        lookup.call(name, arguments)
        elapsed = (time.monotonic() - started) * 1000
        assert elapsed < 250, f"{name} took {elapsed:.0f}ms"


# --- reading -------------------------------------------------------------------------


def test_a_read_is_bounded_and_numbered(lookup: Lookup) -> None:
    text = lookup.read_file("src/ledger.py")
    assert "1: " in text
    assert "def collect" in text
    assert len(text.splitlines()) <= READ_LINES + 2


def test_a_read_starts_where_it_was_asked_to(lookup: Lookup) -> None:
    text = lookup.read_file("src/ledger.py", line=12)
    assert "class Ledger" in text
    assert "def collect" not in text


def test_a_read_past_the_end_says_so_rather_than_returning_nothing(lookup: Lookup) -> None:
    """Silence reads as an empty file, and the model reports what is missing as absent."""
    text = lookup.read_file("src/ledger.py", line=9000)
    assert "past its end" in text


def test_a_missing_file_says_so(lookup: Lookup) -> None:
    assert "no file" in lookup.read_file("src/nothing.py")


def test_a_long_file_is_cut_and_says_it_was_cut(store: Store, tmp_path: Path) -> None:
    where = tmp_path / "wide"
    where.mkdir()
    (where / "one.txt").write_text("\n".join("x" * 400 for _ in range(50)), encoding="utf-8")
    text = Lookup(store, where).read_file("one.txt")
    assert len(text) <= RESULT_CHARS + 200
    assert "cut after" in text


def test_more_lines_below_are_announced(store: Store, tmp_path: Path) -> None:
    where = tmp_path / "long"
    where.mkdir()
    (where / "one.py").write_text("\n".join(f"line {n}" for n in range(500)), encoding="utf-8")
    text = Lookup(store, where).read_file("one.py")
    assert "more lines below" in text


# --- paths a review must not follow --------------------------------------------------


def test_a_path_outside_the_repository_is_refused(lookup: Lookup, tmp_path: Path) -> None:
    """A path in a tool call comes from a model that read a repository the user did not
    necessarily write."""
    (tmp_path / "secret.txt").write_text("token\n", encoding="utf-8")
    assert "no file" in lookup.read_file("../secret.txt")
    assert "no file" in lookup.read_file(str(tmp_path / "secret.txt"))


def test_a_symlink_out_of_the_repository_is_refused(lookup: Lookup, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("token\n", encoding="utf-8")
    (lookup.repository / "escape.txt").symlink_to(tmp_path / "secret.txt")
    assert "no file" in lookup.read_file("escape.txt")


def test_a_directory_is_not_a_file(lookup: Lookup) -> None:
    assert "no file" in lookup.read_file("src")


def test_an_empty_path_is_refused(lookup: Lookup) -> None:
    assert "no file" in lookup.read_file("   ")


# --- searching and symbols -----------------------------------------------------------


def test_search_finds_the_code_that_names_it(lookup: Lookup) -> None:
    text = lookup.search("collect")
    assert "def collect" in text


def test_search_that_finds_nothing_says_so(lookup: Lookup) -> None:
    assert "Nothing indexed" in lookup.search("zzzznotpresent")


def test_symbols_lists_what_a_file_defines(lookup: Lookup) -> None:
    text = lookup.symbols("src/ledger.py")
    assert "collect" in text
    assert "Ledger" in text
    assert "post" in text


def test_symbols_of_a_file_no_grammar_reads_says_so(lookup: Lookup) -> None:
    assert "No grammar" in lookup.symbols("README.md")


# --- callers -------------------------------------------------------------------------


def test_callers_without_an_index_says_so_rather_than_nothing(lookup: Lookup) -> None:
    """ "No callers" and "I cannot look up callers" mean different things to a reviewer."""
    assert "no call-graph index" in lookup.callers("collect")


def test_callers_with_the_feature_off_says_why(store: Store, repository: Path) -> None:
    graph = CodeGraphConfig(enabled=False)
    assert "turned off" in Lookup(store, repository, graph).callers("collect")


# --- the shape the model is shown ----------------------------------------------------


def test_the_notes_describe_the_limits_a_call_really_uses(lookup: Lookup) -> None:
    """A tool whose limits are a surprise gets used badly."""
    notes = lookup.notes()
    assert str(READ_LINES) in notes
    assert str(RESULT_CHARS) in notes
    for name in (READ, SEARCH, SYMBOLS, CALLERS):
        assert name in notes


def test_every_advertised_tool_is_one_it_handles(lookup: Lookup) -> None:
    """A schema that names a tool the dispatcher does not know is a call that always
    fails, and the model spends its ceiling discovering that."""
    for entry in lookup.schema():
        assert lookup.handles(entry["function"]["name"])


def test_a_tool_it_does_not_know_is_answered_rather_than_raised(lookup: Lookup) -> None:
    assert "no nonsense tool" in lookup.call("nonsense", {})

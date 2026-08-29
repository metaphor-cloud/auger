"""The measurement harness. What is tested here is the ground truth, not the models.

A benchmark whose answer key is wrong reports numbers with great confidence, so the
part worth testing is which files `cases_from` calls relevant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bench.retrieval import MIN_BODY, Score, cases_from, compare

from auger.store.db import Store

REPO = "/repo"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    store = Store.open(tmp_path)
    yield store
    store.close()


def chunk(store: Store, path: str, symbol: str, text: str) -> None:
    with store.write() as connection:
        connection.execute(
            "INSERT INTO chunks (repo_path, path, symbol, kind, start_line, end_line, text)"
            " VALUES (?, ?, ?, 'function', 1, 9, ?)",
            (REPO, path, symbol, text),
        )


def body(word: str = "filler") -> str:
    return f"def something():\n    {word} = 1\n" + "    # padding\n" * MIN_BODY


def test_a_symbol_is_asked_about_only_when_other_files_name_it(store: Store) -> None:
    chunk(store, "a.py", "resolve_policy", body())
    for name in ("b.py", "c.py"):
        chunk(store, name, f"uses_{name[0]}", body("resolve_policy"))

    cases = cases_from(store, Path(REPO))
    assert [one.symbol for one in cases] == ["resolve_policy"]
    assert cases[0].relevant == {"b.py", "c.py"}
    assert cases[0].path not in cases[0].relevant, "its own file is never the answer"


def test_a_symbol_nothing_else_mentions_is_dropped(store: Store) -> None:
    """It has no right answer, so scoring against it measures nothing."""
    chunk(store, "a.py", "never_referenced_anywhere", body())
    chunk(store, "b.py", "unrelated_symbol_here", body())
    assert cases_from(store, Path(REPO)) == []


def test_a_word_that_is_everywhere_is_dropped(store: Store) -> None:
    """A name a dozen files mention cannot tell one file from another, and no ranking
    of twelve could cover them all anyway."""
    chunk(store, "a.py", "Configuration", body())
    for index in range(12):
        chunk(store, f"b{index}.py", f"holder_{index}", body("Configuration"))
    assert cases_from(store, Path(REPO)) == []


def test_a_partial_word_is_not_a_mention(store: Store) -> None:
    """`resolve_policy` appearing inside `resolve_policy_cache` is a different symbol."""
    chunk(store, "a.py", "resolve_policy", body())
    chunk(store, "b.py", "uses_it", body("resolve_policy"))
    chunk(store, "c.py", "looks_similar", body("resolve_policy_cache"))
    chunk(store, "d.py", "also_uses_it", body("resolve_policy"))

    cases = cases_from(store, Path(REPO))
    assert cases[0].relevant == {"b.py", "d.py"}


def test_one_long_symbol_split_into_parts_is_asked_about_once(store: Store) -> None:
    """The chunker names the pieces of a long symbol `name part 2`, and counting them
    separately would ask the same question several times."""
    chunk(store, "a.py", "long_running_thing", body())
    chunk(store, "a.py", "long_running_thing part 2", body())
    for name in ("b.py", "c.py"):
        chunk(store, name, f"caller_{name[0]}", body("long_running_thing"))

    cases = cases_from(store, Path(REPO))
    assert [one.symbol for one in cases] == ["long_running_thing"]


def score(model: str, **per_case: float) -> Score:
    return Score(model=model, cases=len(per_case), per_case=dict(per_case))


def test_a_model_that_wins_most_questions_is_called_better() -> None:
    first = score("old", **{f"s{index}": 0.0 for index in range(10)})
    second = score("new", **{f"s{index}": 1.0 for index in range(10)})
    assert "new looks better" in compare(first, second)


def test_winning_a_couple_of_questions_is_not_a_verdict() -> None:
    """An average moves on two lucky questions. The count says whether it should."""
    first = score("old", a=0.0, b=1.0, c=1.0, d=1.0)
    second = score("new", a=1.0, b=1.0, c=1.0, d=0.0)
    assert "no clear difference" in compare(first, second)


def test_two_runs_over_different_symbols_say_so() -> None:
    assert "no question in common" in compare(score("old", a=1.0), score("new", b=1.0))


# --- the review corpus ---------------------------------------------------------------


def test_every_case_is_complete_and_its_defect_is_where_it_says() -> None:
    """A corpus whose answer key points at the wrong line scores every model as bad.

    This reads each case the way the harness does and checks the claim it makes: the
    named file exists on both sides, the two sides differ, and the change is inside the
    span the case points at.
    """
    from bench.review import SLACK, cases

    found = cases()
    assert len(found) >= 12, "a handful of cases separates nothing"
    for case in found:
        before = case.directory / "before" / case.file
        after = case.directory / "after" / case.file
        assert before.is_file(), case.name
        assert after.is_file(), case.name
        old = before.read_text().splitlines()
        new = after.read_text().splitlines()
        assert old != new, f"{case.name} plants no defect"

        changed = [
            index + 1
            for index in range(max(len(old), len(new)))
            if old[index : index + 1] != new[index : index + 1]
        ]
        # At least one changed line has to fall inside the span the case points at.
        # A change may touch an import too, and a finding on the import is not the
        # answer, so this asks that the span covers the defect, not the whole diff.
        first, last = case.lines
        assert changed, case.name
        assert any(first - SLACK <= line <= last + SLACK for line in changed), (
            f"{case.name} says lines {case.lines} but the changes are at {changed}"
        )


def test_every_tier_is_represented() -> None:
    from bench.review import cases

    tiers = {one.tier for one in cases()}
    assert tiers == {1, 2, 3, 4}, tiers


def test_the_answer_never_reaches_the_model() -> None:
    """The reviewer is shown `before` and `after`. If the answer were written in either,
    the harness would measure reading rather than reviewing."""
    from bench.review import cases

    for case in cases():
        for stage in ("before", "after"):
            for entry in (case.directory / stage).rglob("*"):
                if not entry.is_file():
                    continue
                text = entry.read_text(encoding="utf-8", errors="ignore").lower()
                assert case.summary.lower()[:40] not in text, case.name
                for word in ("defect:", "bug:", "vulnerability:", "fixme", "on purpose"):
                    assert word not in text, f"{case.name} gives it away with {word!r}"


def test_a_finding_on_the_right_lines_counts_and_one_elsewhere_does_not() -> None:
    from bench.review import cases

    case = next(one for one in cases() if one.name == "1-off-by-one-page")
    first, last = case.lines
    assert case.hit_by(case.file, first) is True
    assert case.hit_by(case.file, last + 3) is True, "a few lines out is the same defect"
    assert case.hit_by(case.file, first - 60) is False
    assert case.hit_by("other.py", first) is False
    assert case.hit_by(case.file, None) is True, "the file alone identifies one defect"

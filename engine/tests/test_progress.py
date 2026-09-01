"""What the tracker says about work that is still running."""

from __future__ import annotations

from typing import Any

from auger.progress import EVERY, Activity, nowhere


class Clock:
    """A clock a test moves by hand, so a throttle is tested by time and not by sleep."""

    def __init__(self) -> None:
        self.at = 1000.0

    def __call__(self) -> float:
        return self.at


def recorder() -> tuple[list[tuple[str, dict[str, Any]]], Activity, Clock]:
    said: list[tuple[str, dict[str, Any]]] = []
    clock = Clock()
    return said, Activity(lambda event, data: said.append((event, data)), clock), clock


def test_a_run_that_begins_is_reported_at_once() -> None:
    said, activity, _ = recorder()
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    assert [event for event, _ in said] == ["run.progress"]
    assert said[0][1]["slug"] == "acme/alpha"
    assert said[0][1]["kind"] == "diff_review"
    assert [step.slug for step in activity.steps()] == ["acme/alpha"]
    activity.end(watch)
    assert activity.steps() == []
    assert said[-1][1]["phase"] == "done"


def test_a_phase_change_is_always_published() -> None:
    """Two phases back to back inside the throttle window are both reported. A phase
    is what somebody is waiting to read, so it is never the thing that gets dropped."""
    said, activity, _ = recorder()
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.phase("diff")
    watch.phase("index")
    watch.phase("asking")
    assert [data["phase"] for _, data in said] == ["starting", "diff", "index", "asking"]


def test_progress_inside_a_phase_is_published_at_a_bounded_rate() -> None:
    """An embedding loop is thousands of items. Publishing each one would fill every
    subscriber's queue and push the phase changes out of it."""
    said, activity, clock = recorder()
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.phase("embed", total=1000)
    before = len(said)
    for done in range(100):
        watch.advance(done)
    assert len(said) == before, "a tight loop must not publish per item"

    clock.at += EVERY
    watch.advance(100)
    assert len(said) == before + 1
    assert said[-1][1]["done"] == 100
    assert said[-1][1]["total"] == 1000


def test_a_phase_resets_what_the_last_one_counted() -> None:
    said, activity, clock = recorder()
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.phase("embed", total=10)
    clock.at += EVERY
    watch.advance(7)
    watch.phase("asking")
    assert said[-1][1]["done"] == 0
    assert said[-1][1]["total"] == 0
    assert said[-1][1]["tokens"] == 0


def test_tokens_carry_when_they_started() -> None:
    """A count on its own is not a rate. The window needs the moment the answer began
    to turn one into the other."""
    said, activity, clock = recorder()
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.phase("asking")
    watch.tokens(1)
    clock.at += EVERY
    watch.tokens(40)
    assert said[-1][1]["tokens"] == 40
    assert said[-1][1]["tokens_started"] == 1000.0


def test_the_run_id_is_named_as_soon_as_it_exists() -> None:
    said, activity, _ = recorder()
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.names_run("abc123")
    assert said[-1][1]["run"] == "abc123"


def test_two_runs_are_both_live() -> None:
    _, activity, _ = recorder()
    first = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    activity.begin("/repo/beta", "acme/beta", "audit")
    assert [step.slug for step in activity.steps()] == ["acme/alpha", "acme/beta"]
    activity.end(first)
    assert [step.slug for step in activity.steps()] == ["acme/beta"]


def test_a_job_given_no_handle_still_runs() -> None:
    """Every job takes the handle as an option, so the no-op has to answer everything
    a real one does."""
    watch = nowhere()
    watch.phase("asking", detail="anything", total=3)
    watch.advance(1)
    watch.tokens(9)
    watch.names_run("abc123")
    assert watch.step.phase == "asking"

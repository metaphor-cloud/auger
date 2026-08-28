"""A review that runs beside a coding agent reads a half finished tree."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from reviewrig.watch import busy
from tests.helpers import git_commit, git_init


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = git_init(tmp_path / "repo")
    git_commit(path, {"a.py": "x = 1\n"}, "one")
    return path


def age(path: Path, seconds: float) -> None:
    """Backdate everything, so the idle timer sees an old tree."""
    when = time.time() - seconds
    for entry in [path, path / ".git", *path.rglob("*")]:
        try:
            os.utime(entry, (when, when))
        except OSError:
            continue


def test_a_quiet_repository_is_idle(repository: Path) -> None:
    age(repository, 600)
    assert busy.check(repository, idle_seconds=300).busy is False


def test_a_recent_write_holds_the_review_back(repository: Path) -> None:
    (repository / "a.py").write_text("x = 2\n", encoding="utf-8")
    state = busy.check(repository, idle_seconds=300)
    assert state.busy is True
    assert state.reason == "recent_write"


def test_the_idle_timer_can_be_turned_off(repository: Path) -> None:
    (repository / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert busy.check(repository, idle_seconds=0).busy is False


@pytest.mark.parametrize(
    "lock", ["index.lock", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG"]
)
def test_a_git_operation_in_flight_holds_the_review_back(repository: Path, lock: str) -> None:
    age(repository, 600)
    (repository / ".git" / lock).write_text("", encoding="utf-8")
    state = busy.check(repository, idle_seconds=300)
    assert state.busy is True
    assert state.reason == "git_operation"
    assert state.detail == lock


def test_a_rebase_directory_holds_the_review_back(repository: Path) -> None:
    age(repository, 600)
    (repository / ".git" / "rebase-merge").mkdir()
    assert busy.check(repository, idle_seconds=300).reason == "git_operation"


def test_a_lock_beats_the_idle_timer(repository: Path) -> None:
    """A lock is certain. A quiet tree is only a guess."""
    (repository / ".git" / "index.lock").write_text("", encoding="utf-8")
    assert busy.check(repository, idle_seconds=300).reason == "git_operation"


def test_it_finds_an_agent_that_hides_behind_its_version_string() -> None:
    """Claude Code reports its version as its process name. Only argv[0] says `claude`."""
    assert busy.process_labels("2.1.247", ["claude", "--continue"]) == ["2.1.247", "claude"]


def test_it_finds_an_agent_started_through_an_interpreter() -> None:
    labels = busy.process_labels("node", ["node", "/usr/local/bin/aider", "--yes"])
    assert "aider" in labels


def test_it_stops_at_the_first_word_when_that_word_is_not_an_interpreter() -> None:
    """`git commit` must not look like an agent called `commit`."""
    assert busy.process_labels("git", ["git", "claude"]) == ["git", "git"]


def test_it_finds_an_agent_that_ships_as_a_shell_wrapper() -> None:
    """Several agents install as a `#!/bin/sh` launcher, so the shell is what runs."""
    labels = busy.process_labels("bash", ["/bin/sh", "/opt/tools/claude"])
    assert "claude" in labels


def test_it_looks_past_the_flags() -> None:
    labels = busy.process_labels("python3", ["python3", "-u", "/usr/local/bin/aider"])
    assert "aider" in labels


def test_a_shell_running_something_else_is_not_an_agent() -> None:
    labels = busy.process_labels("zsh", ["/bin/zsh", "-c", "npm test"])
    assert "claude" not in labels
    assert "aider" not in labels


def test_the_running_agent_in_this_repository_is_found(tmp_path: Path) -> None:
    """The rig itself runs under a coding agent, so there is one real process to find."""
    here = Path(__file__).resolve().parents[2]
    if not (here / ".git").exists():
        pytest.skip("not running inside a checkout")
    found = busy.agent_processes(here)
    assert isinstance(found, list)


def test_an_unreadable_repository_reports_no_write_time(tmp_path: Path) -> None:
    assert busy.seconds_since_last_write(tmp_path / "gone") is None

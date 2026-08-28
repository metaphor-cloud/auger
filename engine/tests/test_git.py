"""The git reader runs against real git, not a fake."""

from __future__ import annotations

from pathlib import Path

import pytest

from auger.watch import git
from tests.helpers import git_commit, git_init


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = git_init(tmp_path / "repo")
    git_commit(path, {"a.py": "def one():\n    return 1\n"}, "add one")
    return path


def test_it_reads_the_state(repository: Path) -> None:
    state = git.state(repository)
    assert state.branch == "main"
    assert len(state.head) == 40
    assert state.dirty is False


def test_it_sees_an_uncommitted_change(repository: Path) -> None:
    (repository / "a.py").write_text("def one():\n    return 2\n", encoding="utf-8")
    assert git.state(repository).dirty is True


def test_it_reads_the_commits_newest_first(repository: Path) -> None:
    git_commit(repository, {"b.py": "x = 1\n"}, "add two")
    log = git.commits(repository, limit=5)
    assert [commit.subject for commit in log] == ["add two", "add one"]
    assert log[0].author == "Test"


def test_it_reads_the_patch_for_one_commit(repository: Path) -> None:
    git_commit(repository, {"a.py": "def one():\n    return 2\n"}, "change one")
    patch = git.diff(repository, None, "HEAD")
    assert "-    return 1" in patch
    assert "+    return 2" in patch


def test_it_reads_the_patch_for_a_range(repository: Path) -> None:
    base = git.head(repository)
    git_commit(repository, {"b.py": "x = 1\n"}, "two")
    git_commit(repository, {"c.py": "y = 2\n"}, "three")
    patch = git.diff(repository, base, "HEAD")
    assert "b.py" in patch
    assert "c.py" in patch


def test_it_reads_the_uncommitted_work(repository: Path) -> None:
    (repository / "a.py").write_text("def one():\n    return 3\n", encoding="utf-8")
    assert "+    return 3" in git.working_tree_diff(repository)


def test_it_lists_the_changed_files(repository: Path) -> None:
    base = git.head(repository)
    git_commit(repository, {"b.py": "x = 1\n"}, "two")
    assert git.changed_files(repository, base, "HEAD") == ["b.py"]


def test_a_huge_diff_is_capped(repository: Path) -> None:
    """A whole vendored directory must not fill the model's context."""
    git_commit(repository, {"big.txt": "line\n" * 100_000}, "add a large file")
    patch = git.diff(repository, None, "HEAD", max_bytes=2000)
    assert len(patch.encode()) < 2200
    assert "diff truncated" in patch


def test_it_does_not_write_to_the_repository(repository: Path) -> None:
    """A review must never touch the user's repository."""
    before = {
        path.name: path.stat().st_mtime_ns
        for path in (repository / ".git").iterdir()
        if path.is_file()
    }
    git.diff(repository, None, "HEAD")
    git.state(repository)
    after = {
        path.name: path.stat().st_mtime_ns
        for path in (repository / ".git").iterdir()
        if path.is_file()
    }
    assert before == after


def test_every_path_from_repository_content_to_a_command_is_closed() -> None:
    """A diff can run a driver, a hook, or an external command. None of them may run.

    This checks the flags rather than the effect. Making a driver fire needs a config
    file, and `clone` never brings one, so the effect cannot be staged from a clone.
    """
    line = git.command(Path("/repo"), ["diff"])
    assert "--no-optional-locks" in line
    for flag in (
        "core.hooksPath=/dev/null",
        "core.attributesFile=/dev/null",
        "protocol.ext.allow=never",
        "core.fsmonitor=false",
    ):
        assert flag in line, flag
    # An empty `diff.external` is a command that git tries to run, so it must not be set.
    assert "diff.external=" not in line


def test_a_plain_diff_command_still_works(repository: Path) -> None:
    """A hardening flag that breaks every diff is not hardening."""
    git_commit(repository, {"a.py": "def one():\n    return 5\n"}, "change")
    assert "return 5" in git.run(repository, ["diff", "HEAD~1", "HEAD"])


def test_the_environment_carries_no_user_or_system_config() -> None:
    assert git.ENVIRONMENT["GIT_CONFIG_NOSYSTEM"] == "1"
    assert git.ENVIRONMENT["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert git.ENVIRONMENT["GIT_TERMINAL_PROMPT"] == "0"


def test_a_patch_uses_no_textconv_and_no_external_driver(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[list[str]] = []
    real = git.run

    def spy(path: Path, arguments: list[str], timeout: float = 60.0) -> str:
        recorded.append(list(arguments))
        return real(path, arguments, timeout)

    monkeypatch.setattr(git, "run", spy)
    git.diff(repository, None, "HEAD")
    git.working_tree_diff(repository)
    assert recorded
    for arguments in recorded:
        assert "--no-textconv" in arguments
        assert "--no-ext-diff" in arguments


def test_a_missing_repository_raises(tmp_path: Path) -> None:
    with pytest.raises(git.GitError):
        git.head(tmp_path / "gone")

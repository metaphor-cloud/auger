from __future__ import annotations

from pathlib import Path

from auger.discovery.gitdir import read_remote, resolve_git_dir
from auger.models import Remote
from tests.helpers import make_repo


def test_it_reads_the_origin_remote(tmp_path: Path) -> None:
    make_repo(tmp_path / "thing")
    assert read_remote(tmp_path / "thing" / ".git") == Remote("github.com", "acme", "thing")


def test_it_prefers_origin_over_another_remote(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "thing", remote=None)
    (repo / ".git" / "config").write_text(
        '[remote "upstream"]\n\turl = git@github.com:other/up.git\n'
        '[remote "origin"]\n\turl = git@github.com:acme/thing.git\n',
        encoding="utf-8",
    )
    assert read_remote(repo / ".git") == Remote("github.com", "acme", "thing")


def test_it_falls_back_to_another_remote_when_origin_is_absent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "thing", remote=None)
    (repo / ".git" / "config").write_text(
        '[remote "upstream"]\n\turl = git@github.com:other/up.git\n', encoding="utf-8"
    )
    assert read_remote(repo / ".git") == Remote("github.com", "other", "up")


def test_a_repository_with_no_remote_returns_none(tmp_path: Path) -> None:
    make_repo(tmp_path / "thing", remote=None)
    assert read_remote(tmp_path / "thing" / ".git") is None


def test_it_follows_a_worktree_pointer_to_the_shared_config(tmp_path: Path) -> None:
    """A worktree keeps its own directory and shares the config through `commondir`."""
    main = make_repo(tmp_path / "main")
    worktree_git = main / ".git" / "worktrees" / "feature"
    worktree_git.mkdir(parents=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    linked = tmp_path / "feature"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")

    assert resolve_git_dir(linked / ".git") == main / ".git"
    assert read_remote(linked / ".git") == Remote("github.com", "acme", "thing")


def test_a_broken_pointer_returns_none(tmp_path: Path) -> None:
    broken = tmp_path / "thing"
    broken.mkdir()
    (broken / ".git").write_text("not a pointer\n", encoding="utf-8")
    assert read_remote(broken / ".git") is None

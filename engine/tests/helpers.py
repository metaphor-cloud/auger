"""Shared test builders."""

from __future__ import annotations

from pathlib import Path

DEFAULT_REMOTE = "git@github.com:acme/thing.git"


def make_repo(path: Path, remote: str | None = DEFAULT_REMOTE) -> Path:
    """Create a directory that looks like a git checkout."""
    git = path / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    config = "[core]\n\trepositoryformatversion = 0\n"
    if remote:
        config += f'[remote "origin"]\n\turl = {remote}\n\tfetch = +refs/heads/*\n'
    (git / "config").write_text(config, encoding="utf-8")
    return path

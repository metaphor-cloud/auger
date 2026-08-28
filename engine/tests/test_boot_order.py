"""The watcher reads stored repositories, so it must not run before the first walk."""

from __future__ import annotations

from pathlib import Path

import httpx

from reviewrig.models import Remote, Repository
from reviewrig.rig import Rig
from reviewrig.store.repositories import record_scan
from tests.helpers import git_commit, git_init


async def test_a_repository_that_left_the_roots_is_not_reviewed(
    http: httpx.AsyncClient, token: str, rig: Rig, home: Path, tmp_path: Path
) -> None:
    """A stale row from the last run must not send a review at a repository the user
    removed from their roots."""
    gone = tmp_path / "gone"
    git_init(gone, remote="git@github.com:acme/gone.git")
    git_commit(gone, {"a.py": "x = 1\n"}, "one")
    record_scan(rig.store, [Repository(path=gone, remote=Remote("github.com", "acme", "gone"))])
    assert len(rig.repositories()) == 1

    tree = tmp_path / "tree"
    tree.mkdir()
    (home / "config.toml").write_text(f'[[roots]]\npath = "{tree}"\n', encoding="utf-8")

    async with http:
        await http.post("/scan", headers={"Authorization": f"Bearer {token}"})
    assert rig.repositories() == []

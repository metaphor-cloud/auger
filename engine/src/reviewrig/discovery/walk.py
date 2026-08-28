"""Find every git repository under the configured roots."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

import pathspec

from reviewrig.config.schema import DEFAULT_EXCLUDE, Root
from reviewrig.discovery.gitdir import read_remote
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository

GIT_MARKER = ".git"


def _pattern_for_root(root: Path, pattern: str) -> str | None:
    """Rewrite one exclusion so it applies to paths relative to `root`.

    A user writes either a relative pattern such as `**/node_modules/` or an absolute one
    such as `~/git/archive/**`. An absolute pattern that points outside this root cannot
    match anything below it, so it is dropped.
    """
    negated = pattern.startswith("!")
    body = pattern[1:] if negated else pattern
    expanded = Path(body).expanduser()
    if expanded.is_absolute():
        try:
            body = "/" + expanded.relative_to(root).as_posix()
        except ValueError:
            return None
        if pattern.endswith("/"):
            body += "/"
    return ("!" if negated else "") + body


def build_spec(root: Path, patterns: Iterable[str]) -> pathspec.GitIgnoreSpec:
    lines = [_pattern_for_root(root, pattern) for pattern in patterns]
    return pathspec.GitIgnoreSpec.from_lines([line for line in lines if line])


def find_repositories(root: Root, log: Logger | None = None) -> Iterator[Repository]:
    """Yield one `Repository` per git checkout under `root`, deepest excluded first.

    The walk never descends into a repository. A submodule or a nested checkout below a
    repository belongs to that repository's review, not to its own.
    """
    log = log or create_logger("discovery")
    base = root.path
    if not base.is_dir():
        log.warn("root is not a directory", reason="missing_root", path=str(base))
        return
    spec = build_spec(base, [*DEFAULT_EXCLUDE, *root.exclude])

    def on_error(error: OSError) -> None:
        log.warn("directory unreadable", reason="walk_error", path=str(error.filename))

    for dirpath, dirnames, filenames in os.walk(base, topdown=True, onerror=on_error):
        current = Path(dirpath)
        if GIT_MARKER in dirnames or GIT_MARKER in filenames:
            dirnames[:] = []
            yield Repository(path=current, remote=read_remote(current / GIT_MARKER))
            continue
        depth = len(current.relative_to(base).parts)
        if root.max_depth is not None and depth >= root.max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [
            name
            for name in dirnames
            if not spec.match_file(f"{(current / name).relative_to(base).as_posix()}/")
        ]


def scan(roots: Iterable[Root], log: Logger | None = None) -> list[Repository]:
    """Walk every root once. A repository under two roots appears once."""
    seen: dict[Path, Repository] = {}
    for root in roots:
        for repository in find_repositories(root, log):
            seen.setdefault(repository.path, repository)
    return sorted(seen.values(), key=lambda repository: str(repository.path))

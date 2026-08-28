"""Read a repository's remote without a call to `git`.

A scan touches every repository under every root. One subprocess per repository would
cost seconds on a machine that holds hundreds of them, so the scan parses
`.git/config` directly.
"""

from __future__ import annotations

import configparser
from pathlib import Path

from reviewrig.discovery.remote import parse_remote
from reviewrig.models import Remote

PREFERRED_REMOTE = 'remote "origin"'


def resolve_git_dir(marker: Path) -> Path | None:
    """Return the directory that holds `config` for the repository at `marker`.

    `marker` is the `.git` entry. It is a directory in a normal checkout, and a file that
    points elsewhere in a worktree or a submodule.
    """
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    git_dir = Path(text.removeprefix("gitdir:").strip())
    if not git_dir.is_absolute():
        git_dir = (marker.parent / git_dir).resolve()
    # A worktree keeps its own directory but shares the config through `commondir`.
    commondir = git_dir / "commondir"
    if commondir.is_file():
        try:
            common = commondir.read_text(encoding="utf-8").strip()
        except OSError:
            return git_dir
        return (git_dir / common).resolve()
    return git_dir


def read_remote(marker: Path) -> Remote | None:
    """Return the forge coordinates of the repository whose `.git` entry is `marker`."""
    git_dir = resolve_git_dir(marker)
    if git_dir is None:
        return None
    config = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        config.read(git_dir / "config", encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    names = [section for section in config.sections() if section.startswith("remote ")]
    if PREFERRED_REMOTE in names:
        names = [PREFERRED_REMOTE] + [name for name in names if name != PREFERRED_REMOTE]
    for name in names:
        url = config.get(name, "url", fallback="")
        remote = parse_remote(url)
        if remote is not None:
            return remote
    return None

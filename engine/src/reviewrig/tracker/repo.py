"""Which repository a working directory belongs to.

The tracker is started by an agent, in the repository the agent works in, so it reads
its subject from where it stands rather than from a setting nobody would keep current.
"""

from __future__ import annotations

from pathlib import Path

from reviewrig.discovery.walk import GIT_MARKER


def repository_for(start: Path | str) -> Path | None:
    """The git checkout that holds this directory, or None.

    A worktree and a submodule hold `.git` as a file, not a directory, so the test is
    for the entry rather than for a directory.
    """
    current = Path(start).expanduser().absolute()
    for candidate in (current, *current.parents):
        if (candidate / GIT_MARKER).exists():
            return candidate
    return None

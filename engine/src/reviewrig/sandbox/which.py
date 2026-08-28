"""Find a tool that a graphical application cannot see.

An application started from the dock or at login inherits `launchd`'s environment, and
that `PATH` holds none of the places a package manager installs into. A user who ran
`brew install container` would otherwise be told no container runtime exists, and would
silently drop to the weaker sandbox.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

#: Where a package manager puts a binary on macOS and Linux.
EXTRA_PATHS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/run/current-system/sw/bin",
    "/home/linuxbrew/.linuxbrew/bin",
    "~/.local/bin",
)


def find(name: str) -> str | None:
    """The full path to `name`, searching `PATH` and then the usual install places."""
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_PATHS:
        candidate = Path(directory).expanduser() / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None

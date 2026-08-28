"""Decide whether another agent or a person is working in a repository.

A review that runs while a coding agent edits the same tree reads a half finished state
and reports findings about code that no longer exists. The rig waits instead.

Every skip is logged with a reason. A repository that is never reviewed is otherwise
invisible: nothing appears in the UI, and nothing says why.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil

from reviewrig.log import Logger, create_logger
from reviewrig.watch.git import GitError, run

#: Process names that mean a coding agent is at work. The user can add to this list.
AGENT_NAMES: tuple[str, ...] = (
    "claude",
    "codex",
    "aider",
    "cursor-agent",
    "gemini",
    "opencode",
    "goose",
    "amp",
    "crush",
)

#: A file that git leaves behind while an operation is in flight.
GIT_LOCKS: tuple[str, ...] = (
    "index.lock",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "rebase-merge",
    "rebase-apply",
)


@dataclass(frozen=True)
class Busy:
    busy: bool
    reason: str | None = None
    detail: str = ""

    @classmethod
    def idle(cls) -> Busy:
        return cls(busy=False)


def git_operation_in_flight(repository: Path) -> str | None:
    git_dir = repository / ".git"
    if git_dir.is_file():
        return None  # A worktree keeps its state elsewhere. The locks below cover the main one.
    for name in GIT_LOCKS:
        if (git_dir / name).exists():
            return name
    return None


#: An interpreter tells nothing on its own. The name of the script after it does. Several
#: agents ship as a shell wrapper, so the shells belong here too.
INTERPRETERS = frozenset(
    {
        "node",
        "bun",
        "deno",
        "python",
        "python3",
        "ruby",
        "uv",
        "uvx",
        "sh",
        "bash",
        "zsh",
        "dash",
        "fish",
    }
)


def process_labels(name: str, cmdline: Sequence[str]) -> list[str]:
    """Every name that could identify this process.

    The process name is not enough. Claude Code reports its version string as its
    process name, and only the first word of its command line says `claude`. An agent
    that ships as a script hides behind `node`, `python`, or `sh` in the same way.
    """
    labels = [name.lower()]
    words = [word for word in cmdline if word]
    if not words:
        return labels
    first = Path(words[0]).name.lower()
    labels.append(first)
    if first in INTERPRETERS:
        # Skip the flags. `sh -c ...` and `python -u script.py` both hide the name.
        for word in words[1:4]:
            if word.startswith("-"):
                continue
            labels.append(Path(word).name.lower())
            break
    return labels


def _matches(labels: Sequence[str], wanted: set[str]) -> str | None:
    for label in labels:
        stem = label.removesuffix(".js").removesuffix(".py")
        if stem in wanted:
            return stem
    return None


def agent_processes(repository: Path, names: Sequence[str] = AGENT_NAMES) -> list[str]:
    """Matching processes whose working directory is inside `repository`.

    `psutil` needs no extra permission for the user's own processes. If macOS refuses
    anyway, the caller still has the lock and mtime checks.
    """
    wanted = {name.lower() for name in names}
    found: list[str] = []
    for process in psutil.process_iter(["name", "pid", "cmdline"]):
        matched = _matches(
            process_labels(process.info.get("name") or "", process.info.get("cmdline") or []),
            wanted,
        )
        if matched is None:
            continue
        try:
            cwd = Path(process.cwd())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
        if cwd == repository or repository in cwd.parents:
            found.append(f"{matched}({process.info.get('pid')})")
    return found


def seconds_since_last_write(repository: Path) -> float | None:
    """How long ago the working tree last changed. None when it cannot be read.

    Only the files that git reports as changed are stat'd, plus the git directory. A walk
    of the whole tree would cost seconds on a large repository, every cycle.
    """
    newest = 0.0
    git_dir = repository / ".git"
    try:
        newest = max(newest, git_dir.stat().st_mtime)
    except OSError:
        return None
    try:
        changed = run(repository, ["status", "--porcelain=v1", "--untracked-files=normal"])
    except GitError:
        changed = ""
    for line in changed.splitlines():
        name = line[3:].strip().strip('"')
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        try:
            newest = max(newest, (repository / name).stat().st_mtime)
        except OSError:
            continue
    return max(0.0, time.time() - newest)


def check(
    repository: Path,
    idle_seconds: int,
    names: Iterable[str] = AGENT_NAMES,
    log: Logger | None = None,
) -> Busy:
    """Report whether the rig should leave this repository alone for now."""
    log = log or create_logger("watch")
    lock = git_operation_in_flight(repository)
    if lock:
        return Busy(True, "git_operation", lock)

    agents = agent_processes(repository, tuple(names))
    if agents:
        return Busy(True, "agent_running", ", ".join(agents))

    if idle_seconds > 0:
        since = seconds_since_last_write(repository)
        if since is not None and since < idle_seconds:
            return Busy(True, "recent_write", f"{since:.0f}s ago, waiting for {idle_seconds}s")
    return Busy.idle()

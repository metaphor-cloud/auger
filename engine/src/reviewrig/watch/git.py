"""Read a repository with git, without changing it and without running its code.

Git runs on the host, not in the sandbox, and that is a deliberate choice.

`git diff` can run a command through a `textconv` or an external diff driver, but a
driver's command comes from a config file, and `clone` never brings a config file with
it. A hostile repository's `.gitattributes` can name a driver and cannot define one. The
flags below close the remaining paths: no system or global config, and no hooks. Every
command that produces a patch also passes `--no-ext-diff` and `--no-textconv`.

`-c diff.external=` is not among them. An empty value is a command, and git tries to run
it, so it breaks every diff instead of hardening one.

Everything that does run repository-provided code, a build, a dependency install, or a
linter that loads repository rules, runs in the sandbox instead.

Every command is read only. `--no-optional-locks` keeps git from refreshing the index,
so a review never writes to the user's repository.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 60.0

#: Config that closes every path from repository content to a command.
HARDENING = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "protocol.ext.allow=never",
    # The rig reads repositories that the user cloned, and it never writes to them.
    "-c",
    "safe.directory=*",
)

ENVIRONMENT = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_ASKPASS": "",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    author: str
    when: str


@dataclass(frozen=True)
class GitState:
    head: str
    branch: str
    dirty: bool


def command(repository: Path, arguments: Sequence[str]) -> list[str]:
    """The full command line. Kept pure, so a test can read every hardening flag."""
    return ["git", "--no-optional-locks", *HARDENING, "-C", str(repository), *arguments]


def run(repository: Path, arguments: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> str:
    line = command(repository, arguments)
    try:
        completed = subprocess.run(
            line,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=dict(ENVIRONMENT),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git {' '.join(arguments)} failed: {error}") from error
    if completed.returncode != 0:
        raise GitError(f"git {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout


def state(repository: Path) -> GitState:
    head = run(repository, ["rev-parse", "HEAD"]).strip()
    branch = run(repository, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    dirty = bool(run(repository, ["status", "--porcelain"]).strip())
    return GitState(head=head, branch=branch, dirty=dirty)


def head(repository: Path) -> str:
    return run(repository, ["rev-parse", "HEAD"]).strip()


def commits(repository: Path, limit: int = 10, since: str | None = None) -> list[Commit]:
    """Newest first. `since` limits the list to what came after that commit."""
    separator = "\x1f"
    arguments = ["log", f"--pretty=format:%H{separator}%s{separator}%an{separator}%aI"]
    arguments.append(f"{since}..HEAD" if since else f"-{limit}")
    lines = [line for line in run(repository, arguments).splitlines() if line]
    return [Commit(*(line.split(separator, 3))) for line in lines if line.count(separator) == 3][
        :limit
    ]


def changed_files(repository: Path, base: str | None, target: str = "HEAD") -> list[str]:
    if base is None:
        arguments = ["show", "--name-only", "--pretty=format:", target]
    else:
        arguments = ["diff", "--name-only", f"{base}..{target}"]
    return [line for line in run(repository, arguments).splitlines() if line.strip()]


def diff(
    repository: Path,
    base: str | None,
    target: str = "HEAD",
    context_lines: int = 5,
    max_bytes: int = 400_000,
) -> str:
    """The patch for a commit or a range, with no textconv and no external driver."""
    common = [
        "--no-ext-diff",
        "--no-textconv",
        f"--unified={context_lines}",
        "--no-color",
        "--find-renames",
    ]
    if base is None:
        arguments = ["show", *common, "--pretty=format:", target]
    else:
        arguments = ["diff", *common, f"{base}..{target}"]
    return _cap(run(repository, arguments), max_bytes)


def working_tree_diff(repository: Path, context_lines: int = 5, max_bytes: int = 400_000) -> str:
    """What the user has changed and not yet committed, staged and unstaged together."""
    return _cap(
        run(
            repository,
            [
                "diff",
                "HEAD",
                "--no-ext-diff",
                "--no-textconv",
                f"--unified={context_lines}",
                "--no-color",
            ],
        ),
        max_bytes,
    )


def _cap(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= max_bytes:
        return text
    kept = encoded[:max_bytes].decode("utf-8", "ignore")
    return f"{kept}\n\n[diff truncated at {max_bytes} bytes]\n"


def tracked_blobs(repository: Path) -> dict[str, str]:
    """Every tracked file and the sha of its content, straight from the index.

    This is what makes an incremental re-index cheap: a file whose sha did not move
    needs no read, no parse, and no embedding.
    """
    blobs: dict[str, str] = {}
    for line in run(repository, ["ls-files", "--stage", "-z"]).split("\0"):
        if not line:
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 2 and path:
            blobs[path] = parts[1]
    return blobs

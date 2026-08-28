"""What every forge must provide.

The adapters cover the paths that have to be reliable: list the open pull requests, say
which ones are the user's, read a diff, and post a review. Anything beyond that is what
the MCP layer is for.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from auger.config.schema import Forge as ForgeConfig
from auger.log import Logger, create_logger

TOKEN_TIMEOUT = 15.0


class ForgeError(RuntimeError):
    """The forge refused, or could not be reached."""


class NoTokenError(ForgeError):
    """No credential was found for this forge."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    author: str
    url: str
    head_sha: str
    base_ref: str
    draft: bool = False
    assignees: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    updated_at: str = ""

    def concerns(self, user: str) -> bool:
        """True when the user is asked to look at this pull request."""
        return bool(user) and (user in self.assignees or user in self.reviewers)


@dataclass(frozen=True)
class Comment:
    """One finding, placed on a line of the diff."""

    path: str
    line: int | None
    body: str


@dataclass(frozen=True)
class PostedReview:
    id: str
    submitted: bool
    url: str = ""
    comments: int = 0


@dataclass
class Repo:
    """A repository as the forge names it."""

    owner: str
    name: str
    #: GitLab addresses a project by its encoded path or its numeric id.
    project: str = ""

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


class Forge(Protocol):
    kind: str
    host: str

    async def whoami(self) -> str: ...

    async def pull_requests(self, repo: Repo) -> list[PullRequest]: ...

    async def diff(self, repo: Repo, number: int) -> str: ...

    async def post_review(
        self, repo: Repo, pull: PullRequest, summary: str, comments: list[Comment], submit: bool
    ) -> PostedReview: ...


def resolve_token(config: ForgeConfig, log: Logger | None = None) -> str:
    """Find a credential. The environment first, then the forge's own command line tool.

    The token is never logged and never written to the config. A missing token is an
    error the user can act on, not a silent skip.
    """
    log = log or create_logger("forge")
    token = os.environ.get(config.token_env, "").strip()
    if token:
        return token
    if not config.token_command:
        raise NoTokenError(f"set {config.token_env} to use {config.host}")
    try:
        completed = subprocess.run(
            config.token_command,
            capture_output=True,
            text=True,
            timeout=TOKEN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NoTokenError(
            f"set {config.token_env}, or install {config.token_command[0]}: {error}"
        ) from error
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise NoTokenError(
            f"set {config.token_env}, or sign in with "
            f"`{' '.join(config.token_command)}` for {config.host}"
        )
    log.info("forge token found", host=config.host, source=config.token_command[0])
    return token


@dataclass
class ForgeState:
    """What the rig remembers about one forge between polls."""

    user: str = ""
    reachable: bool = False
    reason: str | None = None
    pulls: dict[str, list[PullRequest]] = field(default_factory=dict)

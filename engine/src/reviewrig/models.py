"""Types that cross module boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reviewrig.config.schema import Policy


@dataclass(frozen=True)
class Remote:
    """Where a repository lives on a forge."""

    host: str
    #: Everything between the host and the repository name. A GitLab subgroup keeps its
    #: slashes, for example `group/team`.
    namespace: str
    name: str

    @property
    def org_key(self) -> str:
        """The key that `[org."..."]` sections match, for example `github.com/acme`."""
        return f"{self.host}/{self.namespace}" if self.namespace else self.host

    @property
    def slug(self) -> str:
        return f"{self.org_key}/{self.name}"


@dataclass(frozen=True)
class Repository:
    """One git repository found on disk."""

    path: Path
    remote: Remote | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def org_key(self) -> str | None:
        return self.remote.org_key if self.remote else None

    @property
    def slug(self) -> str:
        return self.remote.slug if self.remote else str(self.path)


@dataclass(frozen=True)
class RepositoryView:
    """A repository with the settings that apply to it."""

    repository: Repository
    policy: Policy

"""Which forge answers for which repository."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from reviewrig.config.schema import Config
from reviewrig.forge import github, gitlab
from reviewrig.forge.base import Forge, ForgeError, ForgeState, Repo, resolve_token
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository

BUILDERS = {"github": github.build, "gitlab": gitlab.build}


@dataclass
class Entry:
    name: str
    forge: Forge
    state: ForgeState


class Registry:
    """The forges the user turned on, keyed by the host in a git remote."""

    def __init__(
        self, config: Config, client: httpx.AsyncClient, log: Logger | None = None
    ) -> None:
        self.log = (log or create_logger("forge")).bind(component="forge")
        self._client = client
        self.entries: dict[str, Entry] = {}
        self.problems: dict[str, str] = {}
        self.reload(config)

    def reload(self, config: Config) -> None:
        self.entries = {}
        self.problems = {}
        for name, settings in config.forge.items():
            if not settings.enabled:
                continue
            builder = BUILDERS.get(settings.kind)
            if builder is None:
                self.problems[name] = f"unknown forge kind {settings.kind!r}"
                continue
            try:
                token = resolve_token(settings, self.log)
            except ForgeError as error:
                # A forge with no credential is a problem the user can fix, so it is
                # reported rather than hidden.
                self.problems[name] = str(error)
                self.log.warn("forge unavailable", reason="no_token", forge=name, error=error)
                continue
            self.entries[settings.host.lower()] = Entry(
                name=name,
                forge=builder(self._client, settings.api, token, settings.host, self.log),
                state=ForgeState(),
            )

    def for_repository(self, repository: Repository) -> tuple[Entry, Repo] | None:
        """The forge and the project name for one checkout, or None when there is none."""
        remote = repository.remote
        if remote is None:
            return None
        entry = self.entries.get(remote.host.lower())
        if entry is None:
            return None
        return entry, Repo(owner=remote.namespace, name=remote.name)

    async def refresh_users(self) -> None:
        """Learn who the user is on each forge. That is what `assigned to me` means."""
        for host, entry in self.entries.items():
            try:
                entry.state.user = await entry.forge.whoami()
                entry.state.reachable = True
                entry.state.reason = None
            except (ForgeError, httpx.HTTPError) as error:
                entry.state.reachable = False
                entry.state.reason = str(error)
                self.log.warn(
                    "forge unreachable", reason="forge_unreachable", host=host, error=error
                )

"""The engine's shared state.

One `Rig` owns the config, the store, and the event bus. Routes and background tasks
read it. Nothing else holds a database handle, so there is one place that knows how to
reload the config and one place that publishes state changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from reviewrig.config import Config, Policy, config_path, ensure_config, load, resolve_policy
from reviewrig.discovery import scan
from reviewrig.events import Event, EventBus
from reviewrig.llm import Gateway, Health, Supervisor, probe_all
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository
from reviewrig.net import Allowlist, Destination, EgressProxy
from reviewrig.sandbox import Selection, select
from reviewrig.settings import Settings
from reviewrig.store import Store
from reviewrig.store.repositories import list_repositories, record_scan


@dataclass(frozen=True)
class RepositoryView:
    """A repository with the settings that apply to it."""

    repository: Repository
    policy: Policy


class Rig:
    def __init__(self, settings: Settings, log: Logger | None = None) -> None:
        self.settings = settings
        self.log = log or create_logger("rig", settings.log_level)
        self.bus = EventBus()
        self.config_path = ensure_config(config_path(settings.home), self.log)
        self.config: Config = load(self.config_path, self.log)
        self.store = Store.open(settings.home)
        self.selection: Selection = select(self.log)
        self.allowlist = Allowlist()
        self._refresh_allowlist()
        self.proxy = EgressProxy(self.allowlist, self.log)
        self.models_dir = settings.home / "models"
        self.supervisor = Supervisor(self.models_dir, self.log)
        self.gateway = Gateway(self.config, self.allowlist, self.log)
        self.health: dict[str, Health] = {}

    async def aclose(self) -> None:
        self.supervisor.stop_all()
        await self.gateway.aclose()
        self.close()

    def close(self) -> None:
        self.store.close()

    def _refresh_allowlist(self) -> None:
        """Add every destination the rig is allowed to reach.

        A backend that sends code off the machine only joins the list when the user has
        turned that on, so a stray `hosted = true` cannot open a path on its own.
        """
        values = list(self.config.egress.allow)
        for backend in self.config.backend.values():
            if backend.hosted and not self.config.egress.allow_hosted:
                continue
            values.append(backend.url)
        for value in values:
            destination = Destination.parse(value)
            if destination:
                self.allowlist.add(destination)

    def reload_config(self) -> Config:
        self.config = load(self.config_path, self.log)
        # The proxy and the gateway hold references, so a reload edits in place rather
        # than replacing what they point at.
        self._refresh_allowlist()
        self.gateway.config = self.config
        self.publish("config.reloaded", roots=len(self.config.roots))
        return self.config

    def publish(self, kind: str, **data: object) -> None:
        self.bus.publish(Event(kind, dict(data)))

    def policy_for(self, repository: Repository) -> Policy:
        return resolve_policy(repository, self.config)

    def scan(self) -> list[RepositoryView]:
        """Walk every root, store what it found, and return the current view."""
        self.publish("scan.started", roots=len(self.config.roots))
        found = scan(self.config.roots, self.log)
        record_scan(self.store, found)
        views = [RepositoryView(repository, self.policy_for(repository)) for repository in found]
        enabled = sum(1 for view in views if view.policy.enabled)
        self.log.info("scan finished", found=len(views), enabled=enabled)
        self.publish("scan.finished", found=len(views), enabled=enabled)
        return views

    async def check_models(self) -> dict[str, Health]:
        """Ask every backend which models it holds. Starts nothing."""
        self.health = await probe_all(self.gateway.client, self.config.backend)
        up = sum(1 for health in self.health.values() if health.up)
        self.publish("models.checked", up=up, total=len(self.health))
        return self.health

    async def ensure_models(self) -> dict[str, Health]:
        """Start any managed backend that does not answer, and wait for it."""
        self.publish("models.starting")
        self.health = await self.supervisor.ensure(self.gateway.client, self.config.backend)
        up = sum(1 for health in self.health.values() if health.up)
        self.publish("models.checked", up=up, total=len(self.health))
        return self.health

    def repositories(self) -> list[RepositoryView]:
        """The stored repositories with their current policy, without a fresh walk."""
        return [
            RepositoryView(repository, self.policy_for(repository))
            for repository in list_repositories(self.store)
        ]

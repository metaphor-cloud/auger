"""The engine's shared state.

One `Rig` owns the config, the store, and the event bus. Routes and background tasks
read it. Nothing else holds a database handle, so there is one place that knows how to
reload the config and one place that publishes state changes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from reviewrig.config import (
    Config,
    Overrides,
    Policy,
    config_path,
    ensure_config,
    load,
    resolve_policy,
    save,
)
from reviewrig.discovery import scan
from reviewrig.events import Event, EventBus
from reviewrig.forge import Registry
from reviewrig.llm import Gateway, Health, Supervisor, probe_all
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository, RepositoryView
from reviewrig.net import Allowlist, Destination, EgressProxy
from reviewrig.sandbox import Selection, select
from reviewrig.schedule import Scheduler, Task, watch, watch_forges
from reviewrig.settings import Settings
from reviewrig.store import Store
from reviewrig.store.repositories import list_repositories, record_scan


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
        self.forges = Registry(self.config, self.gateway.client, self.log)
        self.scheduler = Scheduler(self, self.log)
        self._background: list[asyncio.Task[None]] = []

    async def start_background(self) -> None:
        """Start the workers and the watcher. The UI can connect before this finishes."""
        await self.scheduler.start(self.config.schedule.max_concurrent_reviews)
        self._background.append(asyncio.create_task(watch(self, self.scheduler, self.log)))
        self._background.append(asyncio.create_task(watch_forges(self, self.scheduler, self.log)))

    async def stop_background(self) -> None:
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        self._background.clear()
        await self.scheduler.stop()

    def submit_review(
        self, repository: Repository, base: str | None = None, target: str = "HEAD"
    ) -> bool:
        return self.scheduler.submit(
            Task.review(repository, self.policy_for(repository), base=base, target=target)
        )

    def find_repository(self, path: str) -> Repository | None:
        wanted = Path(path).expanduser().absolute()
        for view in self.repositories():
            if view.repository.path == wanted:
                return view.repository
        return None

    async def aclose(self) -> None:
        await self.stop_background()
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
        # A forge the user turned on must be reachable. One that is off must not be.
        for forge in self.config.forge.values():
            if forge.enabled:
                values.append(forge.api)
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
        self.forges.reload(self.config)
        self.publish("config.reloaded", roots=len(self.config.roots))
        return self.config

    def publish(self, event: str, **data: object) -> None:
        self.bus.publish(Event(event, dict(data)))

    def apply_policy_change(self, level: str, key: str, changes: dict[str, object]) -> Config:
        """Change one settings level and write the file back.

        The write keeps the user's comments, because they edit this file by hand too.
        """
        if level == "defaults":
            self.config.defaults = self.config.defaults.model_copy(update=changes)
        elif level in ("org", "repo"):
            if not key:
                raise ValueError(f"a {level} change needs a key")
            table = self.config.org if level == "org" else self.config.repo
            current = table.get(key, Overrides())
            table[key] = Overrides.model_validate(
                {**current.model_dump(exclude_none=True), **changes}
            )
        else:
            raise ValueError(f"unknown settings level {level!r}")
        save(self.config_path, self.config)
        self.log.info("settings changed", level=level, key=key, fields=sorted(changes))
        self.publish("config.reloaded", roots=len(self.config.roots))
        return self.config

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

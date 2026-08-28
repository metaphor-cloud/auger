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
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository
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

    def close(self) -> None:
        self.store.close()

    def reload_config(self) -> Config:
        self.config = load(self.config_path, self.log)
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

    def repositories(self) -> list[RepositoryView]:
        """The stored repositories with their current policy, without a fresh walk."""
        return [
            RepositoryView(repository, self.policy_for(repository))
            for repository in list_repositories(self.store)
        ]

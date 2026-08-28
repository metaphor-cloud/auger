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
    load_result,
    resolve_policy,
    save,
    set_value,
)
from reviewrig.config.loader import parse
from reviewrig.discovery import scan
from reviewrig.events import Event, EventBus
from reviewrig.forge import Registry
from reviewrig.llm import Gateway, Health, Supervisor, probe_all
from reviewrig.log import Logger, create_logger
from reviewrig.mcp import Access as McpAccess
from reviewrig.mcp import McpRegistry, OAuthError, sign_in
from reviewrig.models import Repository, RepositoryView
from reviewrig.net import Allowlist, Destination, EgressProxy
from reviewrig.sandbox import Selection, select
from reviewrig.schedule import (
    Scheduler,
    Task,
    watch,
    watch_audits,
    watch_forges,
    watch_models,
)
from reviewrig.settings import Settings
from reviewrig.store import Store
from reviewrig.store.repositories import list_repositories, record_scan


class Rig:
    def __init__(self, settings: Settings, log: Logger | None = None) -> None:
        self.settings = settings
        self.log = log or create_logger("rig", settings.log_level)
        self.bus = EventBus()
        self.config_path = ensure_config(config_path(settings.home), self.log)
        loaded = load_result(self.config_path, self.log)
        self.config: Config = loaded.config
        self.config_error: str | None = loaded.error
        self.store = Store.open(settings.home)
        self.selection: Selection = select(self.log)
        self.allowlist = Allowlist()
        self._refresh_allowlist()
        self.proxy = EgressProxy(self.allowlist, self.log)
        self.models_dir = settings.home / "models"
        self.supervisor = Supervisor(self.models_dir, self.log)
        self.gateway = Gateway(self.config, self.allowlist, self.log)
        self.health: dict[str, Health] = {}
        self.setup_running = False
        self.forges = Registry(self.config, self.gateway.client, self.log)
        self.tools = McpRegistry(self.config, self.log, McpAccess(self.allowlist, settings.home))
        self.scheduler = Scheduler(self, self.log)
        self._background: list[asyncio.Task[None]] = []

    #: Every background loop the rig runs. A watcher missing from here never runs, and
    #: nothing else would say so.
    WATCHERS = (watch, watch_forges, watch_audits, watch_models)

    async def start_background(self) -> None:
        """Start the workers and every watcher."""
        await self.scheduler.start(self.config.schedule.max_concurrent_reviews)
        for watcher in self.WATCHERS:
            self._background.append(asyncio.create_task(watcher(self, self.scheduler, self.log)))

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

    def submit_audit(self, repository: Repository) -> bool:
        return self.scheduler.submit(Task.for_audit(repository, self.policy_for(repository)))

    def submit_scan(self, repository: Repository) -> bool:
        return self.scheduler.submit(Task.for_scan(repository, self.policy_for(repository)))

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
        # An http MCP server is a destination like any other. The user attached it, so
        # it is allowed, and a server that is off is not.
        for server in self.config.mcp.values():
            if server.enabled and server.transport == "http" and server.url:
                values.append(server.url)
        for value in values:
            destination = Destination.parse(value)
            if destination:
                self.allowlist.add(destination)

    def reload_config(self) -> Config:
        loaded = load_result(self.config_path, self.log)
        self.config = loaded.config
        self.config_error = loaded.error
        # The proxy and the gateway hold references, so a reload edits in place rather
        # than replacing what they point at.
        self._refresh_allowlist()
        self.gateway.config = self.config
        self.forges.reload(self.config)
        self.tools.reload(self.config)
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

    def set_setting(self, path: str, value: object, remove: bool = False) -> Config:
        """Change one setting by its dotted path, and write the file back.

        One route for every setting. The whole config is validated before anything is
        written, so a change that is wrong is refused with a reason and the file on disk
        is untouched.
        """
        self.config = set_value(self.config, path, value, remove)
        save(self.config_path, self.config)
        self.config_error = None
        self._refresh_allowlist()
        self.gateway.config = self.config
        self.forges.reload(self.config)
        self.tools.reload(self.config)
        self.log.info("setting changed", path=path, removed=remove)
        self.publish("config.reloaded", roots=len(self.config.roots))
        return self.config

    def write_config(self, text: str) -> Config:
        """Replace the whole file. Refuses without writing when it does not parse."""
        config = parse(text)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(text, encoding="utf-8")
        self.config = config
        self.config_error = None
        self._refresh_allowlist()
        self.gateway.config = self.config
        self.forges.reload(self.config)
        self.tools.reload(self.config)
        self.log.info("config replaced", path=str(self.config_path))
        self.publish("config.reloaded", roots=len(self.config.roots))
        return self.config

    def config_text(self) -> str:
        try:
            return self.config_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def change_exclusion(self, pattern: str, remove: bool) -> Config:
        """Add or drop one exclusion, and write the file back."""
        current = list(self.config.exclude)
        if remove:
            current = [entry for entry in current if entry != pattern]
        elif pattern not in current:
            current.append(pattern)
        self.config.exclude = current
        save(self.config_path, self.config)
        self.log.info("exclusions changed", removed=remove, pattern=pattern, count=len(current))
        self.publish("config.reloaded", roots=len(self.config.roots))
        return self.config

    def set_codegraph(self, enabled: bool) -> Config:
        self.config.codegraph = self.config.codegraph.model_copy(update={"enabled": enabled})
        save(self.config_path, self.config)
        self.log.info("call graph changed", enabled=enabled)
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

    async def setup_models(
        self, review_model: str | None = None, embed_model: str | None = None
    ) -> object:
        """Fetch a runtime and weights, write the config, and start the servers.

        This is the path for a machine with nothing installed. It reports every step,
        because the weights are tens of gigabytes and a silent hour looks like a hang.
        """
        from reviewrig.llm import setup

        def report(step: setup.Step) -> None:
            self.publish(
                "setup.progress",
                stage=step.stage,
                name=step.name,
                received=step.received_bytes,
                total=step.total_bytes,
                fraction=round(step.fraction, 4),
                message=step.message,
            )

        self.setup_running = True
        try:
            result = await setup.install(
                self.settings.home,
                self.config,
                review_model,
                embed_model,
                report,
                self.log,
            )
        finally:
            self.setup_running = False
        if result.ok:
            save(self.config_path, self.config)
            self._refresh_allowlist()
            await self.ensure_models()
        self.publish("setup.finished", ok=result.ok, error=result.error)
        return result

    async def sign_in_tool(self, name: str) -> None:
        """Sign in to one MCP server. The user asked, so a browser opens.

        Only this path opens a browser. A review that meets a server it cannot reach
        fails with a reason instead, because a sign in the user did not ask for is a
        surprise they cannot connect to anything they did.
        """
        state = self.tools.servers.get(name)
        if state is None:
            raise OAuthError(f"no MCP server named {name!r}")
        await sign_in(name, state.config, self.settings.home, self.allowlist, self.log)
        await self.tools.refresh({name})
        self.publish("tools.checked", ready=1, total=len(self.tools.servers))

    async def check_tools(self) -> None:
        """Read the tool list from every attached MCP server."""
        await self.tools.refresh()
        ready = sum(1 for state in self.tools.servers.values() if state.reachable)
        self.publish("tools.checked", ready=ready, total=len(self.tools.servers))

    def stop_models(self, name: str | None = None) -> None:
        """Stop one managed model server, or every one of them."""
        if name is None:
            self.supervisor.stop_all()
        else:
            self.supervisor.stop(name)
        self.publish("models.stopped", backend=name or "all")

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

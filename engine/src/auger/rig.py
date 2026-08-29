"""The engine's shared state.

One `Rig` owns the config, the store, and the event bus. Routes and background tasks
read it. Nothing else holds a database handle, so there is one place that knows how to
reload the config and one place that publishes state changes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from auger.config import (
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
from auger.config.loader import parse
from auger.config.schema import JobClass
from auger.discovery import scan
from auger.events import Event, EventBus
from auger.forge import Registry
from auger.jobs.adversary import Argument, argue
from auger.llm import Gateway, Health, Supervisor, probe_all
from auger.llm.setup import SetupResult
from auger.log import Logger, create_logger
from auger.mcp import Access as McpAccess
from auger.mcp import McpRegistry, OAuthError, sign_in
from auger.models import Repository, RepositoryView
from auger.net import Allowlist, Destination, EgressProxy
from auger.sandbox import Selection, select
from auger.schedule import (
    Scheduler,
    Task,
    watch,
    watch_audits,
    watch_forges,
    watch_models,
    watch_verify,
)
from auger.settings import Settings
from auger.store import Store
from auger.store.findings import unjudged
from auger.store.repositories import list_repositories, record_scan
from auger.store.runs import close_interrupted


class Rig:
    """Everything that runs, in one object: the store, the models, the sandbox, the
    forges, the tools, and the queue.

    The name is deliberate, and it outlived the product's old one. A rig is the
    assembly that turns an auger, so this is the thing that drives the reviewing.
    """

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
        #: True while the second model holds the memory. The watchdog leaves the
        #: reviewer alone until it is done, or the two fight over the same gigabytes.
        self.verifying = False
        self.forges = Registry(self.config, self.gateway.client, self.log)
        self.tools = McpRegistry(self.config, self.log, McpAccess(self.allowlist, settings.home))
        self.scheduler = Scheduler(self, self.log)
        self._background: list[asyncio.Task[None]] = []

    #: Every background loop the rig runs. A watcher missing from here never runs, and
    #: nothing else would say so.
    WATCHERS = (watch, watch_forges, watch_audits, watch_models, watch_verify)

    async def start_background(self) -> None:
        """Start the workers and every watcher, stopped.

        Nothing reviews until the user presses play. A rig that starts the moment the
        window opens takes the machine's memory and its fans before anybody asked, and
        the first thing a new user would see is work they did not start. The watchers
        still run, so the queue fills and shows what is waiting.
        """
        # A run left in flight belongs to a process that is gone. Close it before the
        # workers start, or it sits in the list as work that nothing is doing.
        interrupted = await asyncio.to_thread(close_interrupted, self.store)
        if interrupted:
            self.log.warn(
                "runs left in flight were closed",
                reason="interrupted",
                count=interrupted,
            )
        await self.scheduler.start(self.config.schedule.max_concurrent_reviews)
        self.scheduler.pause()
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
        self,
        review_model: str | None = None,
        embed_model: str | None = None,
        adversary_model: str | None = None,
    ) -> object:
        """Fetch a runtime and weights, write the config, and start the servers.

        This is the path for a machine with nothing installed. It reports every step,
        because the weights are tens of gigabytes and a silent hour looks like a hang.
        """
        from auger.llm import setup

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
                adversary_model,
                report,
                self.log,
                self.model_token(),
            )
        finally:
            self.setup_running = False
        #: True while the second model holds the memory. The watchdog leaves the
        #: reviewer alone until it is done, or the two fight over the same gigabytes.
        self.verifying = False
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

    async def fetch_model(
        self,
        repo: str,
        filename: str,
        job_class: str = "review",
        name: str | None = None,
    ) -> SetupResult:
        """Fetch one file and point a job class at it.

        This is the path behind the search: a repository and a file, from wherever the
        user found them, rather than from the list the rig ships with.
        """
        from auger.llm.catalog import CatalogError, Choice, resolve
        from auger.llm.setup import backend_for, models_dir
        from auger.net.download import DownloadError, Progress, client, fetch

        if not repo or not filename:
            raise ValueError("a model needs a repository and a file")
        wanted = JobClass(job_class)
        choice = Choice(
            name=name or filename.removesuffix(".gguf"),
            job_class=wanted,
            repo=repo,
            filename=filename,
            memory_gb=0.0,
            description=f"added from {repo}",
        )
        result = SetupResult()
        token = self.model_token()
        try:
            async with client() as http:
                resolved = await resolve(http, choice, self.log, token)

                def report(progress: Progress) -> None:
                    self.publish(
                        "setup.progress",
                        stage="model",
                        name=progress.name,
                        received=progress.received_bytes,
                        total=progress.total_bytes,
                        fraction=round(progress.fraction, 4),
                        message="",
                    )

                await fetch(
                    http,
                    resolved.url,
                    models_dir(self.settings.home) / choice.filename,
                    resolved.sha256,
                    report,
                    self.log,
                    token,
                )
        except (CatalogError, DownloadError) as error:
            self.log.error("model fetch failed", reason="fetch_failed", repo=repo, error=error)
            result.error = str(error)
            self.publish("setup.finished", ok=False, error=str(error))
            return result

        backend_for(self.config, choice, wanted)
        save(self.config_path, self.config)
        self.reload_config()
        result.review_model = choice.name
        self.publish("setup.finished", ok=True, model=choice.name)
        self.log.info("model added", repo=repo, file=filename, job_class=wanted.value)
        return result

    def model_token(self) -> str | None:
        """The Hugging Face token, read from the environment at the moment of use.

        The config names the variable, never the value, which is the rule the forges
        follow. Nothing writes it to disk and nothing logs it.
        """
        import os

        name = self.config.models.token_env
        return os.environ.get(name) if name else None

    def review_model_state(self) -> tuple[bool, str | None]:
        """Whether a review could run now, and why not.

        The profile decides which backend answers a review, so that is the one to ask
        about. A health record the rig has not written yet means nobody has looked.
        """
        profile = self.config.profile.get(self.config.defaults.model_profile)
        if profile is None:
            return False, f"no model profile named {self.config.defaults.model_profile!r}"
        name = profile.entry(JobClass.REVIEW).backend
        if not name:
            return False, "the profile turns reviewing off. Pick a model in Settings."
        health = self.health.get(name)
        if health is None:
            return False, f"{name} has not been checked yet"
        if health.up:
            return True, None
        return False, health.reason or f"{name} does not answer at {health.url}"

    async def verify_findings(self, limit: int = 200) -> Argument:
        """Swap models, judge every finding that has no verdict, and swap back.

        Two capable models do not fit in memory together, so the reviewer is stopped
        before the second model starts. Nothing reviews while this runs, which is the
        point: a rig that works all day can afford to think about what it found.
        """
        policy = self.config.defaults
        entry = self.config.profile.get(policy.model_profile)
        verify = entry.entry(JobClass.VERIFY).backend if entry else ""
        if not policy.adversary or not verify or verify not in self.config.backend:
            return Argument()

        waiting = await asyncio.to_thread(unjudged, self.store, limit)
        if not waiting:
            return Argument()

        review = entry.entry(JobClass.REVIEW).backend if entry else ""
        self.verifying = True
        self.publish("verify.started", pending=len(waiting), backend=verify)
        try:
            # The reviewer goes first, or the second model has nowhere to load.
            if review and review != verify:
                await asyncio.to_thread(self.stop_models, review)
            self.health = await self.supervisor.ensure(
                self.gateway.client, {verify: self.config.backend[verify]}
            )
            if not self.health.get(verify, Health(name=verify, url="", up=False)).up:
                self.log.warn("verify model did not start", reason="no_model", backend=verify)
                return Argument()
            outcome = await argue(self.store, self.gateway, waiting, policy, self.log)
        finally:
            # Give the memory back either way. The reviewer starts again when a review
            # needs it, which is what `ensure_models` is for.
            await asyncio.to_thread(self.stop_models, verify)
            self.verifying = False
        self.publish(
            "verify.finished",
            judged=outcome.judged,
            rejected=outcome.rejected,
            kept=outcome.kept,
        )
        return outcome

    def stop_models(self, name: str | None = None) -> None:
        """Stop one managed model server, or every one of them."""
        if name is None:
            self.supervisor.stop_all()
        else:
            self.supervisor.stop(name, self.config.backend.get(name))
        self.publish("models.stopped", backend=name or "all")

    async def check_models(self) -> dict[str, Health]:
        """Ask every backend which models it holds. Starts nothing."""
        self.health = await probe_all(self.gateway.client, self.config.backend)
        up = sum(1 for health in self.health.values() if health.up)
        self.publish("models.checked", up=up, total=len(self.health))
        return self.health

    async def ensure_backend(self, name: str) -> Health:
        """Start one managed backend and wait for it, leaving the others alone.

        `ensure_models` would start every one, and two capable models do not fit in
        memory together. A review needs the reviewer, and nothing else.
        """
        backend = self.config.backend.get(name)
        if backend is None:
            return Health(name=name, url="", up=False, reason=f"no backend named {name!r}")
        health = await self.supervisor.ensure(self.gateway.client, {name: backend})
        self.health.update(health)
        return health[name]

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

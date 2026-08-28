"""HTTP surface of the engine.

The engine binds to the loopback address only. Any local process can reach a loopback
port, so every route needs the bearer token that the Tauri host generated. The UI sends
it with fetch, and it reads the event stream with fetch as well, because EventSource
cannot set a header and a token in a URL leaks into logs and history.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from reviewrig import __version__
from reviewrig.api.models import (
    BackendList,
    BackendOut,
    CatalogOut,
    CodeGraphChange,
    ConfigText,
    DashboardOut,
    EgressOut,
    ExcludeChange,
    FindingList,
    FindingOut,
    ForgeList,
    ForgeOut,
    ForgeSetting,
    IndexOut,
    McpServerOut,
    McpServerSetting,
    ModelChoiceOut,
    NoteList,
    NoteOut,
    NoteRequest,
    PolicyChange,
    PolicyLevelOut,
    QueueOut,
    RecordedOut,
    RecordRequest,
    RepositoryList,
    RepositorySummaryOut,
    ReviewRequest,
    RootOut,
    RunList,
    RunOut,
    SandboxOut,
    SettingChange,
    SettingsOut,
    SetupOut,
    SetupRequest,
    StatusRequest,
    SystemOut,
    ToolList,
    ToolOut,
)
from reviewrig.config.schema import JobClass
from reviewrig.events import Event
from reviewrig.log import Logger
from reviewrig.mcp import OAuthError
from reviewrig.rig import Rig
from reviewrig.store.findings import (
    ACTIVE,
    Finding,
    counts,
    list_findings,
    record_one,
    search_findings,
    set_status,
)
from reviewrig.store.index import chunk_count
from reviewrig.store.notes import add_note, notes_for
from reviewrig.store.runs import list_runs
from reviewrig.store.summary import summarise
from reviewrig.tracker import PERSON_SOURCE

BEARER = "bearer "


def _sse(event: Event) -> dict[str, str]:
    return {"event": event.kind, "data": json.dumps(event.data, default=str)}


class TokenAuth:
    """Reject any request that does not carry the host token.

    This is pure ASGI middleware on purpose. Starlette's `BaseHTTPMiddleware` waits for a
    response before it returns, which breaks the endless event stream.
    """

    def __init__(self, app: ASGIApp, token: str, log: Logger) -> None:
        self._app = app
        self._token = token
        self._log = log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        # A preflight carries no credentials by definition, so it cannot carry the token.
        # It also returns no data. Let it through, and let the real request meet the gate.
        if scope.get("method") == "OPTIONS":
            await self._app(scope, receive, send)
            return
        supplied = self._supplied_token(Headers(scope=scope))
        if supplied is None or not secrets.compare_digest(supplied, self._token):
            # Log the reject. A gate that fails closed with no log is an invisible outage.
            self._log.warn(
                "request rejected",
                reason="bad_token" if supplied else "no_token",
                path=scope.get("path", ""),
            )
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    def _supplied_token(headers: Headers) -> str | None:
        header = headers.get("authorization", "")
        if header.lower().startswith(BEARER):
            return header[len(BEARER) :].strip()
        return None


async def _boot(rig: Rig) -> None:
    """Walk the roots, bring the models up, then start the workers.

    The order matters twice. The watcher reads the stored repositories, so starting it
    before the first walk would let it work from the last run's list, which may name a
    repository that no root covers any more. And a review that starts before its model
    is up fails and is recorded as a failure, so the models come up first.
    """
    await asyncio.to_thread(rig.scan)
    # This starts a managed backend that does not answer. A large model takes a minute
    # to load, and the UI is already connected and watching.
    await rig.ensure_models()
    await rig.start_background()


def _first_problem(error: Exception) -> str:
    """One readable sentence out of a validation failure."""
    from pydantic import ValidationError

    if isinstance(error, ValidationError) and error.errors():
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        return f"{where or 'config'}: {first.get('msg', 'is invalid')}"
    return str(error)


def _warnings(rig: Rig) -> list[str]:
    """What needs the user, in the order it should be dealt with."""
    found: list[str] = []
    if rig.config_error:
        found.append(
            f"The config file was refused, so the rig is on its defaults: {rig.config_error}"
        )
    if rig.selection.warning:
        found.append(rig.selection.warning)
    down = [
        name
        for name, backend in rig.config.backend.items()
        if backend.managed and not (rig.health.get(name) and rig.health[name].up)
    ]
    if down:
        found.append(
            f"No model is answering for {', '.join(sorted(down))}. Open Models and press Set up."
        )
    for name, reason in rig.forges.problems.items():
        found.append(f"{name}: {reason}")
    return found


def _dashboard(rig: Rig) -> DashboardOut:
    from datetime import UTC, datetime

    from reviewrig.config import is_excluded

    today = datetime.now(UTC).date().isoformat()
    summary = summarise(rig.store, today)
    views = rig.repositories()
    excluded = sum(1 for view in views if is_excluded(view.repository, rig.config) is not None)
    index = _index_out(rig)
    return DashboardOut(
        version=__version__,
        paused=rig.scheduler.paused,
        pending=rig.scheduler.pending,
        in_flight=rig.scheduler.in_flight,
        workers=rig.config.schedule.max_concurrent_reviews,
        sandbox=SandboxOut(
            backend=rig.selection.sandbox.name,
            degraded=rig.selection.degraded,
            warning=rig.selection.warning,
        ),
        models_up=sum(1 for health in rig.health.values() if health.up),
        models_total=len(rig.config.backend),
        codegraph=rig.config.codegraph.enabled,
        repositories=len(views),
        enabled=sum(1 for view in views if view.policy.enabled),
        excluded=excluded,
        indexed_files=index.files,
        chunks=index.chunks,
        findings=summary.findings,
        suppressed=summary.suppressed,
        dismissed=summary.dismissed,
        runs_today=summary.runs_today,
        runs_by_status=summary.runs_by_status,
        prompt_tokens=summary.prompt_tokens,
        completion_tokens=summary.completion_tokens,
        last_run_at=summary.last_run_at,
        skipped_reasons=summary.skipped_reasons,
        busiest=[
            RepositorySummaryOut(
                path=item.path,
                name=item.name,
                open_findings=item.open_findings,
                worst_severity=item.worst_severity,
                last_run_at=item.last_run_at,
                last_status=item.last_status,
            )
            for item in summary.busiest
        ],
        warnings=_warnings(rig),
    )


def _index_out(rig: Rig) -> IndexOut:
    files = int(rig.store.query("SELECT COUNT(*) AS n FROM indexed_files")[0]["n"])
    embedded = 0
    if rig.store.vectors:
        try:
            embedded = int(rig.store.query("SELECT COUNT(*) AS n FROM chunk_vectors")[0]["n"])
        except Exception:
            embedded = 0
    return IndexOut(
        files=files,
        chunks=chunk_count(rig.store),
        vectors=rig.store.vectors,
        embedded=embedded,
    )


def _queue_out(rig: Rig) -> QueueOut:
    return QueueOut(
        pending=rig.scheduler.pending,
        in_flight=rig.scheduler.in_flight,
        paused=rig.scheduler.paused,
        workers=rig.config.schedule.max_concurrent_reviews,
    )


def _backend_out(rig: Rig, name: str) -> BackendOut:
    backend = rig.config.backend[name]
    health = rig.health.get(name)
    usage = rig.gateway.usage.get(name)
    return BackendOut(
        name=name,
        url=backend.url,
        model=backend.model,
        up=bool(health and health.up),
        hosted=backend.hosted,
        managed=backend.managed,
        models_served=list(health.models) if health else [],
        reason=health.reason if health else "not checked yet",
        requests=usage.requests if usage else 0,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        failures=usage.failures if usage else 0,
    )


def create_app(rig: Rig) -> FastAPI:
    log = rig.log.bind(component="api")
    settings = rig.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await rig.proxy.start()
        # The first walk can take seconds on a large tree, and the model probe waits on
        # the network. Neither should hold up the UI.
        boot = asyncio.create_task(_boot(rig))
        tools_ready = asyncio.create_task(rig.check_tools())
        yield
        boot.cancel()
        tools_ready.cancel()
        await rig.proxy.stop()
        await rig.aclose()

    app = FastAPI(
        title="reviewrig engine",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.rig = rig
    app.add_middleware(TokenAuth, token=settings.token, log=log)
    # Added last, so it wraps the token gate and answers the preflight.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @router.get("/dashboard")
    async def dashboard() -> DashboardOut:
        """One read, so every number on the page is from the same moment."""
        return await asyncio.to_thread(_dashboard, rig)

    @router.get("/system")
    async def system() -> SystemOut:
        stats = rig.proxy.stats
        return SystemOut(
            version=__version__,
            sandbox=SandboxOut(
                backend=rig.selection.sandbox.name,
                degraded=rig.selection.degraded,
                warning=rig.selection.warning,
            ),
            egress=EgressOut(
                proxy_url=rig.proxy.url,
                allowed=[str(destination) for destination in rig.allowlist],
                allowed_requests=stats.allowed,
                refused_requests=stats.refused,
                failed_requests=stats.failed,
                recently_refused=list(stats.refused_hosts),
            ),
            index=await asyncio.to_thread(_index_out, rig),
            image=rig.config.image,
            config_error=rig.config_error,
        )

    def backend_list() -> BackendList:
        return BackendList(
            backends=[_backend_out(rig, name) for name in sorted(rig.config.backend)],
            profiles={
                name: {job_class.value: profile.entry(job_class).backend for job_class in JobClass}
                for name, profile in rig.config.profile.items()
            },
            active_profile_backends={
                job_class.value: rig.config.profile["balanced"].entry(job_class).backend
                for job_class in JobClass
            }
            if "balanced" in rig.config.profile
            else {},
            allow_hosted=rig.config.egress.allow_hosted,
        )

    @router.get("/models/catalog")
    async def model_catalog() -> CatalogOut:
        from reviewrig.llm import catalog, runtime
        from reviewrig.llm.setup import models_dir

        usable = catalog.usable_memory_gb()
        here = models_dir(rig.settings.home)
        return CatalogOut(
            models=[
                ModelChoiceOut(
                    name=choice.name,
                    job_class=choice.job_class.value,
                    repo=choice.repo,
                    filename=choice.filename,
                    memory_gb=choice.memory_gb,
                    description=choice.description,
                    fits=choice.memory_gb <= usable,
                    downloaded=catalog.downloaded(choice, here),
                )
                for choice in catalog.CATALOG
            ],
            recommended=catalog.recommended_review_model(None, here).name,
            usable_memory_gb=round(usable, 1),
            runtime_installed=runtime.resolve(rig.settings.home) is not None,
            setup_running=rig.setup_running,
        )

    @router.post("/models/setup")
    async def setup_models(request: SetupRequest) -> SetupOut:
        """Fetch a runtime and weights for a machine that has neither."""
        if rig.setup_running:
            raise HTTPException(status_code=409, detail="a setup is already running")
        result = await rig.setup_models(request.model or None, request.embed or None)
        return SetupOut(
            ok=result.ok,  # type: ignore[attr-defined]
            review_model=result.review_model,  # type: ignore[attr-defined]
            embed_model=result.embed_model,  # type: ignore[attr-defined]
            rerank_model=result.rerank_model,  # type: ignore[attr-defined]
            runtime_path=result.runtime_path,  # type: ignore[attr-defined]
            error=result.error,  # type: ignore[attr-defined]
        )

    @router.get("/models")
    async def models() -> BackendList:
        return backend_list()

    @router.post("/models/check")
    async def check_models() -> BackendList:
        await rig.check_models()
        return backend_list()

    @router.post("/models/start")
    async def start_models() -> BackendList:
        """Start any managed backend that does not answer. A large model takes a minute."""
        await rig.ensure_models()
        return backend_list()

    @router.get("/forges")
    async def forges() -> ForgeList:
        rows: list[ForgeOut] = []
        for name, settings in sorted(rig.config.forge.items()):
            entry = rig.forges.entries.get(settings.host.lower())
            rows.append(
                ForgeOut(
                    name=name,
                    kind=settings.kind,
                    host=settings.host,
                    enabled=settings.enabled,
                    reachable=bool(entry and entry.state.reachable),
                    user=entry.state.user if entry else "",
                    reason=rig.forges.problems.get(name) or (entry.state.reason if entry else None),
                )
            )
        return ForgeList(forges=rows)

    @router.get("/tools")
    async def tools() -> ToolList:
        return ToolList(
            servers=[
                McpServerOut(
                    name=name,
                    transport=state.config.transport,
                    target=state.config.url or state.config.command,
                    reachable=state.reachable,
                    reason=state.reason,
                    tools=[
                        ToolOut(
                            server=tool.server,
                            name=tool.name,
                            qualified=tool.qualified,
                            description=tool.description,
                        )
                        for tool in state.tools
                    ],
                    needs_sign_in=state.needs_sign_in,
                    signed_in=state.needs_sign_in and rig.tools.signed_in(name),
                )
                for name, state in sorted(rig.tools.servers.items())
            ],
            allowed=list(rig.config.defaults.tools),
        )

    @router.post("/tools/check")
    async def check_tools() -> ToolList:
        await rig.check_tools()
        return await tools()

    @router.post("/tools/{name}/sign-in")
    async def sign_in_tool(name: str) -> ToolList:
        """Sign in to one MCP server, in a browser, because the user asked.

        A review never reaches this route. It is the only place a browser opens.
        """
        try:
            await rig.sign_in_tool(name)
        except OAuthError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return await tools()

    @router.get("/settings")
    async def settings_view() -> SettingsOut:
        levels = [
            PolicyLevelOut(level="org", key=key, overrides=value.model_dump(exclude_none=True))
            for key, value in sorted(rig.config.org.items())
        ] + [
            PolicyLevelOut(level="repo", key=key, overrides=value.model_dump(exclude_none=True))
            for key, value in sorted(rig.config.repo.items())
        ]
        from reviewrig.sandbox.which import find

        return SettingsOut(
            defaults=rig.config.defaults,
            levels=levels,
            config_path=str(rig.config_path),
            exclude=list(rig.config.exclude),
            codegraph=rig.config.codegraph.enabled,
            codegraph_available=find(rig.config.codegraph.command) is not None,
            roots=[
                RootOut(path=str(root.path), exclude=list(root.exclude), max_depth=root.max_depth)
                for root in rig.config.roots
            ],
            mcp=[
                McpServerSetting(
                    name=name,
                    transport=server.transport,
                    target=server.url or server.command,
                    enabled=server.enabled,
                )
                for name, server in sorted(rig.config.mcp.items())
            ],
            forges=[
                ForgeSetting(name=name, host=forge.host, enabled=forge.enabled)
                for name, forge in sorted(rig.config.forge.items())
            ],
            schedule=rig.config.schedule.model_dump(mode="json"),
            allow_hosted=rig.config.egress.allow_hosted,
        )

    @router.put("/settings")
    async def change_settings(change: PolicyChange) -> SettingsOut:
        """Write one level back to the config file, keeping the user's comments."""
        try:
            await asyncio.to_thread(
                rig.apply_policy_change, change.level, change.key, change.changes
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return await settings_view()

    @router.put("/settings/value")
    async def change_setting(change: SettingChange) -> SettingsOut:
        """Change any setting by its dotted path.

        One route rather than a form per key, so nothing in the config file is beyond
        the UI's reach.
        """
        try:
            await asyncio.to_thread(rig.set_setting, change.path, change.value, change.remove)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_first_problem(error)) from error
        return await settings_view()

    @router.get("/settings/raw", response_class=PlainTextResponse)
    async def read_config() -> str:
        return await asyncio.to_thread(rig.config_text)

    @router.put("/settings/raw")
    async def write_config(body: ConfigText) -> SettingsOut:
        """Replace the whole file. Nothing is written when it does not parse."""
        try:
            await asyncio.to_thread(rig.write_config, body.text)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_first_problem(error)) from error
        return await settings_view()

    @router.put("/settings/exclude")
    async def change_exclude(change: ExcludeChange) -> SettingsOut:
        """Add or drop one entry of the exclusion list."""
        pattern = change.pattern.strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="an exclusion needs a pattern")
        await asyncio.to_thread(rig.change_exclusion, pattern, change.remove)
        return await settings_view()

    @router.put("/settings/codegraph")
    async def change_codegraph(change: CodeGraphChange) -> SettingsOut:
        """Turn the call graph source on or off for every repository."""
        await asyncio.to_thread(rig.set_codegraph, change.enabled)
        return await settings_view()

    @router.get("/findings")
    async def findings(
        repo: str | None = None,
        status: str = "open,doing",
        limit: int = 500,
        include_dismissed: bool = False,
        query: str = "",
    ) -> FindingList:
        """Open findings. A finding the model judged false is hidden unless asked for."""
        statuses = [part for part in status.split(",") if part]
        if query.strip():
            rows = await asyncio.to_thread(search_findings, rig.store, query, repo, statuses, limit)
        else:
            rows = await asyncio.to_thread(
                list_findings, rig.store, repo, statuses, limit, include_dismissed
            )
        return FindingList(
            findings=[FindingOut.of(finding) for finding in rows],
            counts=await asyncio.to_thread(counts, rig.store, repo),
        )

    @router.post("/findings/status")
    async def change_status(request: StatusRequest) -> FindingList:
        """Suppress a finding, or bring it back. Suppression survives a re-review."""
        await asyncio.to_thread(set_status, rig.store, request.fingerprints, request.status)
        rig.publish("findings.changed", count=len(request.fingerprints), status=request.status)
        rows = await asyncio.to_thread(list_findings, rig.store, None, ACTIVE, 500)
        return FindingList(
            findings=[FindingOut.of(finding) for finding in rows],
            counts=await asyncio.to_thread(counts, rig.store, None),
        )

    @router.post("/findings")
    async def record_item(request: RecordRequest) -> RecordedOut:
        """Record one work item by hand.

        The same store the tracker writes to, so a person and an agent see one list.
        """
        if not request.title.strip():
            raise HTTPException(status_code=400, detail="an item needs a title")
        stored, existed = await asyncio.to_thread(
            record_one,
            rig.store,
            Finding(
                repo_path=request.repo_path,
                source=PERSON_SOURCE,
                severity=request.severity,
                title=request.title.strip(),
                detail=request.detail.strip(),
                file=request.file.strip(),
                line=request.line,
            ),
        )
        rig.publish("findings.changed", count=1, status=stored.status)
        return RecordedOut(item=FindingOut.of(stored), existed=existed)

    @router.get("/findings/{item}/notes")
    async def item_notes(item: str) -> NoteList:
        rows = await asyncio.to_thread(notes_for, rig.store, item)
        return NoteList(
            notes=[
                NoteOut(id=note.id, author=note.author, written_at=note.written_at, text=note.text)
                for note in rows
            ]
        )

    @router.post("/findings/{item}/notes")
    async def add_item_note(item: str, request: NoteRequest) -> NoteList:
        """Append one note. A note is never edited, so the journal stays a record."""
        try:
            await asyncio.to_thread(add_note, rig.store, item, request.text, PERSON_SOURCE)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return await item_notes(item)

    @router.get("/runs")
    async def runs(repo: str | None = None, limit: int = 100) -> RunList:
        rows = await asyncio.to_thread(list_runs, rig.store, repo, limit)
        return RunList(runs=[RunOut.of(run) for run in rows])

    @router.get("/queue")
    async def queue() -> QueueOut:
        return _queue_out(rig)

    @router.post("/queue/pause")
    async def pause() -> QueueOut:
        rig.scheduler.pause()
        rig.publish("queue.paused", pending=rig.scheduler.pending)
        return _queue_out(rig)

    @router.post("/queue/resume")
    async def resume() -> QueueOut:
        rig.scheduler.resume()
        rig.publish("queue.resumed", pending=rig.scheduler.pending)
        return _queue_out(rig)

    @router.post("/scan/security")
    async def request_scan(request: ReviewRequest) -> QueueOut:
        """Queue a Semgrep scan and its triage for one repository."""
        repository = rig.find_repository(request.path)
        if repository is None:
            raise HTTPException(status_code=404, detail=f"no repository at {request.path}")
        rig.submit_scan(repository)
        rig.publish("queue.changed", pending=rig.scheduler.pending)
        return _queue_out(rig)

    @router.post("/audit")
    async def request_audit(request: ReviewRequest) -> QueueOut:
        """Queue a whole repository audit."""
        repository = rig.find_repository(request.path)
        if repository is None:
            raise HTTPException(status_code=404, detail=f"no repository at {request.path}")
        rig.submit_audit(repository)
        rig.publish("queue.changed", pending=rig.scheduler.pending)
        return _queue_out(rig)

    @router.post("/review")
    async def request_review(request: ReviewRequest) -> QueueOut:
        """Queue a review by hand. The watcher queues the rest on its own."""
        repository = rig.find_repository(request.path)
        if repository is None:
            raise HTTPException(status_code=404, detail=f"no repository at {request.path}")
        rig.submit_review(repository, base=request.base, target=request.target)
        rig.publish("queue.changed", pending=rig.scheduler.pending)
        return _queue_out(rig)

    @router.get("/repositories")
    async def repositories() -> RepositoryList:
        return RepositoryList.of(await asyncio.to_thread(rig.repositories))

    @router.post("/scan")
    async def rescan() -> RepositoryList:
        """Reload the config and walk every root again."""
        await asyncio.to_thread(rig.reload_config)
        return RepositoryList.of(await asyncio.to_thread(rig.scan))

    @router.get("/events")
    async def events() -> EventSourceResponse:
        # Do not poll `request.is_disconnected()` here. It reads the same ASGI receive
        # channel that EventSourceResponse watches for the disconnect, and the two
        # readers steal messages from each other. EventSourceResponse cancels this
        # generator when the client goes away.
        async def stream() -> AsyncIterator[dict[str, str]]:
            with rig.bus.subscribe() as subscription:
                log.info("event stream opened", subscribers=rig.bus.subscriber_count)
                try:
                    yield _sse(Event("hello", {"version": __version__}))
                    async for event in subscription:
                        yield _sse(event)
                finally:
                    log.info("event stream closed", subscribers=rig.bus.subscriber_count - 1)

        return EventSourceResponse(stream())

    app.include_router(router)
    return app

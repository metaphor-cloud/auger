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
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from reviewrig import __version__
from reviewrig.api.models import (
    BackendList,
    BackendOut,
    EgressOut,
    FindingList,
    FindingOut,
    ForgeList,
    ForgeOut,
    IndexOut,
    PolicyChange,
    PolicyLevelOut,
    QueueOut,
    RepositoryList,
    ReviewRequest,
    RunList,
    RunOut,
    SandboxOut,
    SettingsOut,
    StatusRequest,
    SystemOut,
)
from reviewrig.config.schema import JobClass
from reviewrig.events import Event
from reviewrig.log import Logger
from reviewrig.rig import Rig
from reviewrig.store.findings import counts, list_findings, set_status
from reviewrig.store.index import chunk_count
from reviewrig.store.runs import list_runs

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
    """Walk the roots, then start the workers.

    The order matters. The watcher reads the stored repositories, so starting it before
    the first walk would let it work from the last run's list, which may name a
    repository that no root covers any more.
    """
    await asyncio.to_thread(rig.scan)
    await rig.start_background()


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
        models = asyncio.create_task(rig.check_models())
        yield
        boot.cancel()
        models.cancel()
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

    @router.get("/settings")
    async def settings_view() -> SettingsOut:
        levels = [
            PolicyLevelOut(level="org", key=key, overrides=value.model_dump(exclude_none=True))
            for key, value in sorted(rig.config.org.items())
        ] + [
            PolicyLevelOut(level="repo", key=key, overrides=value.model_dump(exclude_none=True))
            for key, value in sorted(rig.config.repo.items())
        ]
        return SettingsOut(
            defaults=rig.config.defaults,
            levels=levels,
            config_path=str(rig.config_path),
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

    @router.get("/findings")
    async def findings(
        repo: str | None = None, status: str = "open", limit: int = 500
    ) -> FindingList:
        statuses = [part for part in status.split(",") if part]
        rows = await asyncio.to_thread(list_findings, rig.store, repo, statuses, limit)
        return FindingList(
            findings=[FindingOut.of(finding) for finding in rows],
            counts=await asyncio.to_thread(counts, rig.store, repo),
        )

    @router.post("/findings/status")
    async def change_status(request: StatusRequest) -> FindingList:
        """Suppress a finding, or bring it back. Suppression survives a re-review."""
        await asyncio.to_thread(set_status, rig.store, request.fingerprints, request.status)
        rig.publish("findings.changed", count=len(request.fingerprints), status=request.status)
        rows = await asyncio.to_thread(list_findings, rig.store, None, ["open"], 500)
        return FindingList(
            findings=[FindingOut.of(finding) for finding in rows],
            counts=await asyncio.to_thread(counts, rig.store, None),
        )

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

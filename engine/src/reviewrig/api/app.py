"""HTTP surface of the engine.

The engine binds to the loopback address only. Any local process can reach a loopback
port, so every route needs the bearer token that the Tauri host generated. The UI sends
it with fetch, and it reads the event stream with fetch as well, because EventSource
cannot set a header and a token in a URL leaks into logs and history.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from reviewrig import __version__
from reviewrig.events import Event, EventBus
from reviewrig.log import Logger, create_logger
from reviewrig.settings import Settings

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


def create_app(settings: Settings, logger: Logger | None = None) -> FastAPI:
    log = logger or create_logger("api", settings.log_level)
    bus = EventBus()
    app = FastAPI(title="reviewrig engine", version=__version__, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.bus = bus
    app.state.log = log
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

    @router.get("/events")
    async def events() -> EventSourceResponse:
        # Do not poll `request.is_disconnected()` here. It reads the same ASGI receive
        # channel that EventSourceResponse watches for the disconnect, and the two
        # readers steal messages from each other. EventSourceResponse cancels this
        # generator when the client goes away.
        async def stream() -> AsyncIterator[dict[str, str]]:
            with bus.subscribe() as subscription:
                log.info("event stream opened", subscribers=bus.subscriber_count)
                try:
                    yield _sse(Event("hello", {"version": __version__}))
                    async for event in subscription:
                        yield _sse(event)
                finally:
                    log.info("event stream closed", subscribers=bus.subscriber_count - 1)

        return EventSourceResponse(stream())

    app.include_router(router)
    return app

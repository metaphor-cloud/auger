"""The engine's only HTTP client.

Every outbound request from the engine goes through this transport, and it refuses any
destination that is not on the allowlist before a single byte leaves. The proxy covers
subprocesses. This covers the engine itself, which is the process that holds the code.
"""

from __future__ import annotations

import httpx

from reviewrig.log import Logger, create_logger
from reviewrig.net.allowlist import Allowlist


class EgressRefused(httpx.RequestError):
    """The destination is not on the allowlist."""


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        allowlist: Allowlist,
        inner: httpx.AsyncBaseTransport | None = None,
        log: Logger | None = None,
    ) -> None:
        self._allowlist = allowlist
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._log = (log or create_logger("egress")).bind(component="egress")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        if not self._allowlist.allows(host, port):
            self._log.warn(
                "egress refused",
                reason="not_allowlisted",
                destination=f"{host}:{port}",
                method=request.method,
            )
            raise EgressRefused(f"{host}:{port} is not on the allowlist", request=request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_client(
    allowlist: Allowlist,
    log: Logger | None = None,
    timeout: float = 600.0,
    **kwargs: object,
) -> httpx.AsyncClient:
    """An httpx client that can only reach the allowlist."""
    return httpx.AsyncClient(
        transport=GuardedTransport(allowlist, log=log),
        timeout=timeout,
        follow_redirects=False,  # A redirect could point off the allowlist.
        **kwargs,  # type: ignore[arg-type]
    )

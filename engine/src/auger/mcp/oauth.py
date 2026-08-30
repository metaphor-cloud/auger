"""Signing in to an MCP server that asks for OAuth.

Two rules shape this file.

A background review never opens a browser. The rig reviews code while the user is doing
something else, so a sign in that appears on its own would be both a surprise and a
prompt the user cannot connect to any action. The flow therefore runs only when the user
asks for it, from the Tools view. A review uses the stored token, refreshes it silently
when it can, and fails with a clear reason when it cannot.

A token is a credential, so it is written to a file only the user can read, and it is
never written to the config file, never logged, and never passed to a server process.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import socketserver
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from auger.config.schema import McpServer
from auger.log import Logger, create_logger
from auger.net.allowlist import Allowlist, Destination

#: How long the rig waits for the user to finish in the browser.
SIGN_IN_TIMEOUT = 300.0

DONE_PAGE = b"""<!doctype html>
<title>auger</title>
<body style="font-family: system-ui; padding: 3rem">
<h1>Signed in</h1>
<p>You can close this tab and go back to auger.</p>
"""


class OAuthError(RuntimeError):
    """The sign in did not finish."""


def store_dir(home: Path) -> Path:
    """Where the tokens live. The directory is the user's alone."""
    here = home / "oauth"
    here.mkdir(parents=True, exist_ok=True)
    os.chmod(here, 0o700)
    return here


def store_path(home: Path, name: str) -> Path:
    # A server name comes from the config file, so it is quoted into one path segment.
    safe = "".join(character if character.isalnum() else "-" for character in name)
    return store_dir(home) / f"{safe}.json"


class FileTokenStorage(TokenStorage):
    """Tokens and client registration for one server, on disk at 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def _write(self, body: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create the file closed to everyone else before anything is written to it.
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(body, file)

    async def get_tokens(self) -> OAuthToken | None:
        stored = self._read().get("tokens")
        return OAuthToken.model_validate(stored) if stored else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        body = self._read()
        body["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(body)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        stored = self._read().get("client")
        return OAuthClientInformationFull.model_validate(stored) if stored else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        body = self._read()
        body["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(body)


def signed_in(home: Path, name: str) -> bool:
    """Whether a token is stored. It says nothing about whether it still works."""
    try:
        body = json.loads(store_path(home, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(body, dict) and body.get("tokens"))


def forget(home: Path, name: str) -> None:
    store_path(home, name).unlink(missing_ok=True)


def redirect_uri(config: McpServer) -> str:
    return f"http://127.0.0.1:{config.callback_port}/callback"


def client_metadata(config: McpServer) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name="auger",
        redirect_uris=[redirect_uri(config)],  # type: ignore[list-item]
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=config.scope or None,
        token_endpoint_auth_method="client_secret_post",
    )


async def _refuse_browser(url: str) -> None:
    """What a background review does when the server asks the user to sign in.

    It refuses. Opening a browser behind the user's back is worse than a failed run,
    and the failed run says what to do about it.
    """
    raise OAuthError("this server needs you to sign in. Open Tools and press Sign in.")


async def _refuse_callback() -> AuthorizationCodeResult:
    raise OAuthError("this server needs you to sign in. Open Tools and press Sign in.")


def background_provider(name: str, config: McpServer, home: Path) -> OAuthClientProvider:
    """The provider a review uses. It refreshes a token, and never asks for one."""
    return OAuthClientProvider(
        server_url=config.url,
        client_metadata=client_metadata(config),
        storage=FileTokenStorage(store_path(home, name)),
        redirect_handler=_refuse_browser,
        callback_handler=_refuse_callback,
    )


class LoopbackHTTPServer(HTTPServer):
    """`HTTPServer` without the reverse lookup its constructor does.

    `HTTPServer.server_bind` calls `socket.getfqdn` on the address it binds, and it
    does so before the server is listening. On a machine whose resolver does not
    answer for 127.0.0.1 that blocks for tens of seconds, and the sign in appears to
    hang between the click and the browser opening.

    The name is only ever used to build a Host header this server never sends, and
    the address is a literal we chose, so there is nothing to look up.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[0], self.server_address[1]
        self.server_name = host if isinstance(host, str) else host.decode()
        self.server_port = int(port)


@dataclass
class Callback:
    """A one request web server on the loopback address, for the redirect."""

    port: int
    result: asyncio.Future[AuthorizationCodeResult]
    _server: HTTPServer
    _thread: threading.Thread

    @classmethod
    def start(cls, port: int, log: Logger) -> Callback:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[AuthorizationCodeResult] = loop.create_future()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = parse_qs(urlsplit(self.path).query)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(DONE_PAGE)
                if result.done():
                    return
                error = query.get("error", [""])[0]
                if error:
                    loop.call_soon_threadsafe(
                        result.set_exception,
                        OAuthError(f"the authorization server refused: {error}"),
                    )
                    return
                loop.call_soon_threadsafe(
                    result.set_result,
                    AuthorizationCodeResult(
                        code=query.get("code", [""])[0],
                        state=query.get("state", [None])[0],
                        iss=query.get("iss", [None])[0],
                    ),
                )

            def log_message(self, template: str, *args: Any) -> None:
                # The default handler writes to stderr, which is the engine's log.
                log.debug("oauth callback", path=self.path)

        try:
            server = LoopbackHTTPServer(("127.0.0.1", port), Handler)
        except OSError as error:
            raise OAuthError(f"port {port} is not free for the sign in: {error}") from error
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return cls(port=port, result=result, _server=server, _thread=thread)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)


def free_port(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


async def sign_in(
    name: str,
    config: McpServer,
    home: Path,
    allowlist: Allowlist,
    log: Logger | None = None,
    open_url: Any = None,
) -> None:
    """Run the authorization code flow once, and store what comes back.

    The user asked for this, so a browser opens. Everything else about the flow obeys
    the same allowlist as the rest of the engine: an authorization server on a host the
    rig may not reach is refused, and the reason names the host to add.
    """
    log = (log or create_logger("mcp")).bind(component="oauth", server=name)
    if config.transport != "http" or not config.url:
        raise OAuthError("only an http server signs in")

    # The flow reaches the server and its authorization server, so both must be allowed.
    destination = Destination.parse(config.url)
    if destination:
        allowlist.add(destination)

    callback = Callback.start(config.callback_port, log)
    opened: list[str] = []

    async def redirect(url: str) -> None:
        opened.append(url)
        (open_url or webbrowser.open)(url)
        log.info("sign in started", server=name)

    async def wait() -> AuthorizationCodeResult:
        return await asyncio.wait_for(callback.result, SIGN_IN_TIMEOUT)

    provider = OAuthClientProvider(
        server_url=config.url,
        client_metadata=client_metadata(config),
        storage=FileTokenStorage(store_path(home, name)),
        redirect_handler=redirect,
        callback_handler=wait,
    )
    from auger.net.client import guarded_mcp_client

    try:
        async with guarded_mcp_client(allowlist, log, auth=provider) as client:
            # Any authenticated request drives the whole flow, and this one is the
            # cheapest: the transport initialises the session on it.
            response = await client.get(config.url, headers={"Accept": "text/event-stream"})
            if response.status_code >= 400 and not signed_in(home, name):
                raise OAuthError(f"the server answered {response.status_code} after sign in")
    except OAuthError:
        raise
    except Exception as error:
        log.warn("sign in failed", reason="oauth_failed", error=error)
        raise OAuthError(str(error)) from error
    finally:
        callback.stop()

    if not signed_in(home, name):
        raise OAuthError("the sign in finished without a token")
    log.info("signed in", server=name)

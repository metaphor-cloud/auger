"""Signing in to an MCP server that asks for OAuth.

The flow runs against a real authorization server on the loopback address, not a mock
of the client, so the test proves what a user would see: a browser opens once, a token
is stored, and the token is only readable by the user.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from auger.config.schema import McpServer
from auger.mcp.client import Access
from auger.mcp.oauth import (
    OAuthError,
    background_provider,
    sign_in,
    signed_in,
    store_path,
)
from auger.net.allowlist import Allowlist, Destination
from auger.net.client import guarded_mcp_client

TOKEN = "test-access-token"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class FakeServer:
    """An MCP endpoint and its authorization server, on one loopback port."""

    def __init__(self) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.registrations = 0
        self.token_requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def reply(self, status: int, body: dict[str, Any], headers: dict[str, str]) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/.well-known/oauth-protected-resource":
                    self.reply(
                        200,
                        {
                            "resource": f"{outer.base}/mcp",
                            "authorization_servers": [outer.base],
                        },
                        {},
                    )
                elif path == "/.well-known/oauth-authorization-server":
                    self.reply(
                        200,
                        {
                            "issuer": outer.base,
                            "authorization_endpoint": f"{outer.base}/authorize",
                            "token_endpoint": f"{outer.base}/token",
                            "registration_endpoint": f"{outer.base}/register",
                            "response_types_supported": ["code"],
                            "grant_types_supported": ["authorization_code", "refresh_token"],
                            "code_challenge_methods_supported": ["S256"],
                        },
                        {},
                    )
                elif path == "/mcp":
                    if self.headers.get("Authorization") == f"Bearer {TOKEN}":
                        self.reply(200, {"ok": True}, {})
                    else:
                        self.reply(
                            401,
                            {"error": "unauthorized"},
                            {
                                "WWW-Authenticate": (
                                    "Bearer resource_metadata="
                                    f'"{outer.base}/.well-known/oauth-protected-resource"'
                                )
                            },
                        )
                else:
                    self.reply(404, {"error": "not found"}, {})

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if path == "/register":
                    outer.registrations += 1
                    self.reply(
                        201,
                        {
                            "client_id": "test-client",
                            "client_secret": "test-secret",
                            "redirect_uris": [f"http://127.0.0.1:{outer.callback_port}/callback"],
                            "grant_types": ["authorization_code", "refresh_token"],
                            "response_types": ["code"],
                            "token_endpoint_auth_method": "client_secret_post",
                        },
                        {},
                    )
                elif path == "/token":
                    outer.token_requests += 1
                    self.reply(
                        200,
                        {
                            "access_token": TOKEN,
                            "token_type": "Bearer",
                            "expires_in": 3600,
                            "refresh_token": "test-refresh",
                        },
                        {},
                    )
                else:
                    self.reply(404, {"error": "not found"}, {})

            def log_message(self, template: str, *args: Any) -> None:
                return

        self.callback_port = free_port()
        self._server = HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> FakeServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def server() -> Any:
    fake = FakeServer().start()
    yield fake
    fake.stop()


def visit(url: str) -> None:
    """What the browser does: follow the authorization URL back to the rig."""
    query = parse_qs(urlsplit(url).query)
    callback = f"{query['redirect_uri'][0]}?code=test-code&state={query['state'][0]}"
    urllib.request.urlopen(callback, timeout=10).read()


@pytest.mark.asyncio
async def test_sign_in_stores_a_token(server: Any, tmp_path: Path) -> None:
    config = McpServer(
        transport="http",
        url=f"{server.base}/mcp",
        auth="oauth",
        callback_port=server.callback_port,
    )
    allowlist = Allowlist()

    def open_url(url: str) -> None:
        threading.Thread(target=visit, args=(url,), daemon=True).start()

    await sign_in("acme", config, tmp_path, allowlist, open_url=open_url)

    assert signed_in(tmp_path, "acme")
    assert server.registrations == 1
    assert server.token_requests == 1
    stored = json.loads(store_path(tmp_path, "acme").read_text())
    assert stored["tokens"]["access_token"] == TOKEN


@pytest.mark.asyncio
async def test_the_token_file_is_the_user_s_alone(server: Any, tmp_path: Path) -> None:
    config = McpServer(
        transport="http",
        url=f"{server.base}/mcp",
        auth="oauth",
        callback_port=server.callback_port,
    )

    def open_url(url: str) -> None:
        threading.Thread(target=visit, args=(url,), daemon=True).start()

    await sign_in("acme", config, tmp_path, Allowlist(), open_url=open_url)

    path = store_path(tmp_path, "acme")
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "oauth").stat().st_mode & 0o777 == 0o700
    assert os.access(path, os.R_OK)


@pytest.mark.asyncio
async def test_a_review_never_opens_a_browser(tmp_path: Path) -> None:
    """A background review that meets a signed out server fails, and says why."""
    config = McpServer(transport="http", url="https://tools.example.com/mcp", auth="oauth")
    provider = background_provider("acme", config, tmp_path)
    redirect = provider.context.redirect_handler
    assert redirect is not None

    with pytest.raises(OAuthError) as raised:
        await redirect("https://tools.example.com/authorize")
    assert "sign in" in str(raised.value).lower()


@pytest.mark.asyncio
async def test_a_tool_server_off_the_allowlist_is_refused(tmp_path: Path) -> None:
    """An http MCP server is a destination like any other, so the guard applies."""
    allowlist = Allowlist([Destination("127.0.0.1", 8080)])
    async with guarded_mcp_client(allowlist) as client:
        with pytest.raises(httpx2.RequestError) as raised:
            await client.get("https://tools.example.com/mcp")
    assert "not on the allowlist" in str(raised.value)


@pytest.mark.asyncio
async def test_a_sign_in_that_never_comes_back_gives_up(server: Any, tmp_path: Path) -> None:
    """A user who closes the browser tab gets an error, not a wait forever."""
    from auger.mcp import oauth

    config = McpServer(
        transport="http",
        url=f"{server.base}/mcp",
        auth="oauth",
        callback_port=server.callback_port,
    )
    original = oauth.SIGN_IN_TIMEOUT
    oauth.SIGN_IN_TIMEOUT = 0.5
    try:
        with pytest.raises(OAuthError):
            await sign_in("acme", config, tmp_path, Allowlist(), open_url=lambda url: None)
    finally:
        oauth.SIGN_IN_TIMEOUT = original
    assert not signed_in(tmp_path, "acme")


@pytest.mark.asyncio
async def test_a_stored_token_is_used_without_a_browser(server: Any, tmp_path: Path) -> None:
    """The second run does not sign in again. That is the point of storing it."""
    config = McpServer(
        transport="http",
        url=f"{server.base}/mcp",
        auth="oauth",
        callback_port=server.callback_port,
    )

    def open_url(url: str) -> None:
        threading.Thread(target=visit, args=(url,), daemon=True).start()

    allowlist = Allowlist([Destination("127.0.0.1", server.port)])
    await sign_in("acme", config, tmp_path, allowlist, open_url=open_url)
    before = server.token_requests

    provider = background_provider("acme", config, tmp_path)
    async with guarded_mcp_client(allowlist, auth=provider) as client:
        response = await client.get(f"{server.base}/mcp")

    assert response.status_code == 200
    assert server.token_requests == before


def test_access_defaults_to_an_empty_allowlist() -> None:
    """Nothing is reachable unless the rig says so."""
    assert len(Access().allowlist) == 0


@pytest.mark.asyncio
async def test_a_timeout_does_not_leave_the_callback_port_open(server: Any, tmp_path: Path) -> None:
    from auger.mcp import oauth

    config = McpServer(
        transport="http",
        url=f"{server.base}/mcp",
        auth="oauth",
        callback_port=server.callback_port,
    )
    original = oauth.SIGN_IN_TIMEOUT
    oauth.SIGN_IN_TIMEOUT = 0.3
    try:
        with pytest.raises(OAuthError):
            await sign_in("acme", config, tmp_path, Allowlist(), open_url=lambda url: None)
    finally:
        oauth.SIGN_IN_TIMEOUT = original
    await asyncio.sleep(0.1)
    assert oauth.free_port(server.callback_port)

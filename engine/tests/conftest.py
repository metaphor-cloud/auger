from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from reviewrig.api import create_app
from reviewrig.rig import Rig
from reviewrig.settings import Settings


@pytest.fixture
def token() -> str:
    return "test-token-not-a-secret"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """An isolated reviewrig home with no roots.

    A test must never walk the developer's own tree, so the config starts empty and each
    test adds the roots it needs.
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('[defaults]\nmode = "draft"\n', encoding="utf-8")
    return home


@pytest.fixture
def settings(token: str, home: Path) -> Settings:
    return Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home)


@pytest.fixture
def rig(settings: Settings) -> Iterator[Rig]:
    rig = Rig(settings)
    yield rig
    rig.close()


@pytest.fixture
def app(rig: Rig) -> FastAPI:
    return create_app(rig)


@pytest.fixture
def http(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://engine")


@pytest.fixture
async def base_url(app: FastAPI) -> AsyncIterator[str]:
    """Serve the application on a real loopback port.

    The in-process ASGI transport collects the whole body before it returns, so it can
    never read an endless stream. The SSE route needs a real socket.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task

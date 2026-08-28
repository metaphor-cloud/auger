from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from reviewrig.api import create_app
from reviewrig.settings import Settings


@pytest.fixture
def token() -> str:
    return "test-token-not-a-secret"


@pytest.fixture
def settings(token: str) -> Settings:
    return Settings(host="127.0.0.1", port=0, token=token, log_level="debug")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


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

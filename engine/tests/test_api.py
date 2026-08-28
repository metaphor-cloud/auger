from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from reviewrig import __version__
from reviewrig.events import Event


async def test_health_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        response = await http.get("/health")
    assert response.status_code == 401


async def test_health_rejects_a_wrong_token(http: httpx.AsyncClient) -> None:
    async with http:
        response = await http.get("/health", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_health_accepts_the_token(http: httpx.AsyncClient, token: str, scheme: str) -> None:
    async with http:
        response = await http.get("/health", headers={"Authorization": f"{scheme} {token}"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_event_stream_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        response = await http.get("/events")
    assert response.status_code == 401


async def read_event(lines: AsyncIterator[str]) -> tuple[str, dict[str, object]]:
    """Read one server-sent event. A blank line ends an event."""
    kind = ""
    data = ""
    async for line in lines:
        if line == "":
            break
        field, _, value = line.partition(":")
        if field == "event":
            kind = value.strip()
        elif field == "data":
            data = value.strip()
    return kind, json.loads(data)


@asynccontextmanager
async def event_stream(base_url: str, token: str) -> AsyncIterator[AsyncIterator[str]]:
    async with (
        httpx.AsyncClient(base_url=base_url, timeout=10) as client,
        client.stream("GET", "/events", headers={"Authorization": f"Bearer {token}"}) as response,
    ):
        assert response.status_code == 200
        yield response.aiter_lines()


async def test_event_stream_opens_with_a_greeting(base_url: str, token: str) -> None:
    async with event_stream(base_url, token) as lines:
        assert await read_event(lines) == ("hello", {"version": __version__})


async def test_event_stream_delivers_a_published_event(
    app: FastAPI, base_url: str, token: str
) -> None:
    async with event_stream(base_url, token) as lines:
        await read_event(lines)
        app.state.rig.bus.publish(Event("run.started", {"repo": "a"}))
        assert await read_event(lines) == ("run.started", {"repo": "a"})

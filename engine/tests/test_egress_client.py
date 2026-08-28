from __future__ import annotations

import httpx
import pytest

from auger.net import Allowlist, EgressRefused, GuardedTransport


def guarded(values: list[str], handler: object) -> httpx.AsyncClient:
    inner = httpx.MockTransport(handler)  # type: ignore[arg-type]
    transport = GuardedTransport(Allowlist.from_values(values), inner=inner)
    return httpx.AsyncClient(transport=transport)


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="reached")


async def test_it_allows_a_listed_destination() -> None:
    async with guarded(["http://127.0.0.1:8080"], ok) as client:
        response = await client.get("http://127.0.0.1:8080/v1/models")
    assert response.text == "reached"


async def test_it_refuses_an_unlisted_destination_before_any_byte_leaves() -> None:
    reached = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reached
        reached = True
        return httpx.Response(200)

    async with guarded(["http://127.0.0.1:8080"], handler) as client:
        with pytest.raises(EgressRefused):
            await client.post("https://api.openai.com/v1/messages", json={"code": "secret"})
    assert reached is False


async def test_the_port_is_part_of_the_match() -> None:
    async with guarded(["http://127.0.0.1:8080"], ok) as client:
        with pytest.raises(EgressRefused):
            await client.get("http://127.0.0.1:9090/v1/models")


async def test_the_refusal_is_logged_with_a_reason(capsys: pytest.CaptureFixture[str]) -> None:
    async with guarded([], ok) as client:
        with pytest.raises(EgressRefused):
            await client.get("https://evil.example/")
    error = capsys.readouterr().err
    assert '"reason": "not_allowlisted"' in error
    assert '"destination": "evil.example:443"' in error

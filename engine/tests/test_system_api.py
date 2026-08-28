from __future__ import annotations

from typing import Any

import httpx


async def system(http: httpx.AsyncClient, token: str) -> Any:
    response = await http.get("/system", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_it_names_the_sandbox_backend(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        body = await system(http, token)
    assert body["sandbox"]["backend"] in {"apple-container", "podman", "docker", "seatbelt"}


async def test_a_degraded_sandbox_carries_a_warning(http: httpx.AsyncClient, token: str) -> None:
    """The user must know when analysis dropped to Seatbelt."""
    async with http:
        body = await system(http, token)
    sandbox = body["sandbox"]
    assert sandbox["degraded"] is (sandbox["backend"] == "seatbelt")
    assert (sandbox["warning"] is not None) is sandbox["degraded"]


async def test_it_reports_the_egress_allowlist(
    http: httpx.AsyncClient, token: str, home: object
) -> None:
    async with http:
        body = await system(http, token)
    assert body["egress"]["allowed"] == []
    assert body["egress"]["refused_requests"] == 0


async def test_it_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/system")).status_code == 401

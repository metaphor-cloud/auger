from __future__ import annotations

import httpx

ORIGIN = "tauri://localhost"


async def test_a_preflight_needs_no_token(http: httpx.AsyncClient) -> None:
    """The browser sends no credentials on a preflight, so the gate must let it pass."""
    async with http:
        response = await http.request(
            "OPTIONS",
            "/health",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


async def test_a_preflight_from_another_origin_is_refused(http: httpx.AsyncClient) -> None:
    async with http:
        response = await http.request(
            "OPTIONS",
            "/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in response.headers


async def test_the_real_request_still_needs_the_token(http: httpx.AsyncClient) -> None:
    async with http:
        response = await http.get("/health", headers={"Origin": ORIGIN})
    assert response.status_code == 401


async def test_an_authorised_request_carries_the_origin_header(
    http: httpx.AsyncClient, token: str
) -> None:
    async with http:
        response = await http.get(
            "/health", headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN

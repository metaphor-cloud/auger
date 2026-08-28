from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


async def get(http: httpx.AsyncClient, token: str, path: str) -> Any:
    response = await http.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_it_lists_the_backends_and_what_the_profile_sends_where(
    http: httpx.AsyncClient, token: str
) -> None:
    async with http:
        body = await get(http, token, "/models")
    names = [backend["name"] for backend in body["backends"]]
    assert names == ["local-embed", "local-rerank", "local-review", "local-triage"]
    assert body["active_profile_backends"]["review"] == "local-review"
    assert body["active_profile_backends"]["triage"] == "local-triage"


async def test_a_backend_that_is_not_running_says_why(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        await http.post("/models/check", headers={"Authorization": f"Bearer {token}"})
        body = await get(http, token, "/models")
    review = next(item for item in body["backends"] if item["name"] == "local-review")
    assert review["up"] is False
    assert review["reason"]


async def test_hosted_use_is_off_until_the_user_turns_it_on(
    http: httpx.AsyncClient, token: str
) -> None:
    async with http:
        body = await get(http, token, "/models")
    assert body["allow_hosted"] is False


async def test_the_ui_can_see_a_custom_profile(
    http: httpx.AsyncClient, token: str, home: Path
) -> None:
    (home / "config.toml").write_text(
        '[profile.fast.review]\nbackend = "local-triage"\n', encoding="utf-8"
    )
    async with http:
        await http.post("/scan", headers={"Authorization": f"Bearer {token}"})
        body = await get(http, token, "/models")
    assert body["profiles"]["fast"]["review"] == "local-triage"


async def test_the_routes_need_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/models")).status_code == 401
        assert (await http.post("/models/check")).status_code == 401

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from reviewrig.rig import Rig


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
    assert names == ["local-embed", "local-review"]
    assert body["active_profile_backends"]["review"] == "local-review"
    # Reranking is off by default: it needs a reranker model, and retrieval works without.
    assert body["active_profile_backends"]["rerank"] == ""


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
        '[profile.fast.review]\nbackend = "local-embed"\n', encoding="utf-8"
    )
    async with http:
        await http.post("/scan", headers={"Authorization": f"Bearer {token}"})
        body = await get(http, token, "/models")
    assert body["profiles"]["fast"]["review"] == "local-embed"


async def test_the_routes_need_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/models")).status_code == 401
        assert (await http.post("/models/check")).status_code == 401


async def test_the_catalogue_says_what_this_machine_can_hold(
    http: httpx.AsyncClient, token: str
) -> None:
    async with http:
        body = await get(http, token, "/models/catalog")
    assert body["usable_memory_gb"] > 0
    assert body["recommended"] in {model["name"] for model in body["models"]}
    review = [model for model in body["models"] if model["job_class"] == "review"]
    assert len(review) >= 2
    assert any(model["fits"] for model in review)


async def test_a_model_too_large_for_this_machine_is_marked(
    http: httpx.AsyncClient, token: str
) -> None:
    """A user must not start an hour of downloading for weights that will not load."""
    async with http:
        body = await get(http, token, "/models/catalog")
    for model in body["models"]:
        assert model["fits"] == (model["memory_gb"] <= body["usable_memory_gb"])


async def test_two_setups_do_not_run_at_once(http: httpx.AsyncClient, token: str, rig: Rig) -> None:
    rig.setup_running = True
    async with http:
        response = await http.post(
            "/models/setup", headers={"Authorization": f"Bearer {token}"}, json={"model": ""}
        )
    assert response.status_code == 409


async def test_the_catalogue_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/models/catalog")).status_code == 401
        assert (await http.post("/models/setup", json={})).status_code == 401

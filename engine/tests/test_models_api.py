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


async def test_a_backend_that_is_not_running_says_why(
    http: httpx.AsyncClient, token: str, home: Path, rig: Rig
) -> None:
    """Port 1 is reserved and nothing can listen on it, so this cannot depend on luck.

    The default backend is 8080, and a model server the developer happens to be running
    would otherwise make this pass or fail by accident.
    """
    (home / "config.toml").write_text(
        '[backend.local-review]\nurl = "http://127.0.0.1:1/v1"\n', encoding="utf-8"
    )
    rig.reload_config()
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


async def test_a_managed_server_can_be_stopped_to_free_the_memory(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    """A review model holds tens of gigabytes. The user must be able to take it back."""

    class Fake:
        name = "local-review"

        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    running = Fake()
    rig.supervisor.running["local-review"] = running  # type: ignore[assignment]

    async with http:
        listed = await get(http, token, "/models")
        before = next(one for one in listed["backends"] if one["name"] == "local-review")
        assert before["ours"] is True

        response = await http.post(
            "/models/stop?name=local-review", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        after = next(one for one in response.json()["backends"] if one["name"] == "local-review")

    assert running.stopped is True
    assert rig.supervisor.running == {}
    # The window offers no unload for a server this process did not start.
    assert after["ours"] is False


async def test_stopping_every_model_leaves_nothing_running(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    class Fake:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self) -> None:
            return

    rig.supervisor.running["local-review"] = Fake("local-review")  # type: ignore[assignment]
    rig.supervisor.running["local-embed"] = Fake("local-embed")  # type: ignore[assignment]

    async with http:
        response = await http.post("/models/stop", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert rig.supervisor.running == {}


async def test_a_rig_that_already_watches_something_is_past_its_first_run(
    http: httpx.AsyncClient, token: str, home: Path, rig: Rig, tmp_path: Path
) -> None:
    """An existing user must never be shown the first run wizard again."""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (home / "config.toml").write_text(f'[[roots]]\npath = "{tmp_path}"\n', encoding="utf-8")
    rig.reload_config()
    rig.scan()

    async with http:
        body = await get(http, token, "/onboarding")
    assert body["repositories"] == 1
    assert body["done"] is True


async def test_the_first_run_reports_what_is_still_missing(
    http: httpx.AsyncClient, token: str
) -> None:
    async with http:
        body = await get(http, token, "/onboarding")
        assert body["done"] is False
        assert body["roots"] == 0

        response = await http.put(
            "/onboarding", headers={"Authorization": f"Bearer {token}"}, json={"done": True}
        )
        assert response.json()["done"] is True

        assert (await get(http, token, "/onboarding"))["done"] is True

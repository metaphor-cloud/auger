from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from auger.rig import Rig


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
    # Whether anything fits is a fact about the machine, and a CI runner holds less
    # memory than any laptop this runs on. What has to hold everywhere is that the
    # claim agrees with the memory the same answer reports, and that a machine where
    # nothing fits is still told what to run.
    usable = body["usable_memory_gb"]
    assert all(model["fits"] == (model["memory_gb"] <= usable) for model in review)


async def test_the_catalogue_says_which_model_each_job_class_runs_now(
    http: httpx.AsyncClient, token: str, home: Path, rig: Rig
) -> None:
    """The window seeds its pickers from this. Without it the page opens showing the
    recommendation, and a choice that was saved looks as though it was not."""
    (home / "config.toml").write_text(
        "[backend.local-review]\n"
        'model = "a-chosen-reviewer"\n'
        "[backend.local-adversary]\n"
        'url = "http://127.0.0.1:1340/v1"\n'
        'model = "a-chosen-adversary"\n'
        "[profile.balanced.verify]\n"
        'backend = "local-adversary"\n',
        encoding="utf-8",
    )
    rig.reload_config()
    async with http:
        body = await get(http, token, "/models/catalog")
    assert body["chosen"]["review"] == "a-chosen-reviewer"
    assert body["chosen"]["verify"] == "a-chosen-adversary"
    assert body["chosen"]["review"] != body["recommended"], "a recommendation is not a choice"


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

        def signal(self) -> None:
            return

        def wait(self, timeout: float = 0.0) -> None:
            return

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


async def test_play_is_refused_when_no_model_can_answer(
    http: httpx.AsyncClient, token: str, home: Path, rig: Rig
) -> None:
    """A queue that runs with no model gives one failed run per repository and no
    explanation. Refuse, and say what to do instead."""
    (home / "config.toml").write_text(
        '[backend.local-review]\nurl = "http://127.0.0.1:1/v1"\nmanaged = false\n',
        encoding="utf-8",
    )
    rig.reload_config()
    rig.scheduler.pause()  # The window opens stopped, so this is the real starting state.

    async with http:
        await http.post("/models/check", headers={"Authorization": f"Bearer {token}"})
        state = await get(http, token, "/queue")
        assert state["models_ready"] is False
        assert state["models_reason"]

        response = await http.post("/queue/resume", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert rig.scheduler.paused is True


async def test_a_run_left_in_flight_is_closed_on_the_next_start(home: Path, token: str) -> None:
    """Only a crash or a quit leaves a run running. It must not sit there forever."""
    from auger.settings import Settings
    from auger.store.runs import list_runs, start

    rig = Rig(Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home))
    try:
        start(rig.store, Path("/tmp/repo"), "diff_review", None, "HEAD")
        assert [one.status for one in list_runs(rig.store)] == ["running"]

        await rig.start_background()
        closed = list_runs(rig.store)
    finally:
        await rig.aclose()

    assert [one.status for one in closed] == ["failed"]
    assert [one.reason for one in closed] == ["interrupted"]


async def test_it_searches_for_a_model_to_run(
    http: httpx.AsyncClient, token: str, monkeypatch: Any
) -> None:
    """The recommended list is the expectation. This is everything else."""
    from auger.llm import sources

    class Fake:
        name = "huggingface"

        async def search(self, http: Any, query: str) -> list[sources.Repository]:
            assert query == "qwen coder"
            return [
                sources.Repository(
                    source="huggingface",
                    id="acme/thing-GGUF",
                    downloads=9,
                    likes=1,
                    gated=True,
                    updated="2026-08-01",
                )
            ]

        async def files(self, http: Any, repo: str) -> list[sources.File]:
            return [sources.File(name="thing-Q4_K_M.gguf", size_bytes=4_000_000_000)]

    monkeypatch.setattr("auger.api.app.source_for", lambda *a, **k: Fake())

    async with http:
        found = await get(http, token, "/models/search?q=qwen%20coder")
        files = await get(http, token, "/models/files?repo=acme/thing-GGUF")

    assert found["results"][0]["id"] == "acme/thing-GGUF"
    assert found["results"][0]["gated"] is True
    assert found["token_env"] == "HF_TOKEN"
    assert files["files"][0]["gigabytes"] == 4.0


async def test_a_source_that_will_not_answer_says_so(
    http: httpx.AsyncClient, token: str, monkeypatch: Any
) -> None:
    from auger.llm.sources import SourceError

    class Refuses:
        name = "huggingface"

        async def search(self, http: Any, query: str) -> Any:
            raise SourceError("acme/thing is gated. Accept its licence, then set a token.")

    monkeypatch.setattr("auger.api.app.source_for", lambda *a, **k: Refuses())

    async with http:
        response = await http.get(
            "/models/search?q=x", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 502
    assert "gated" in response.json()["detail"]


def test_the_token_is_read_from_the_environment_not_the_config(rig: Rig, monkeypatch: Any) -> None:
    """The config names the variable. It never holds the value."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert rig.model_token() is None
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    assert rig.model_token() == "hf_secret"
    assert "hf_secret" not in rig.config_path.read_text(encoding="utf-8")

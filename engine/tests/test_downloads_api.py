"""The download queue and the second engine, over HTTP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from auger.downloads import Item
from auger.llm import coli
from auger.net.download import Digest
from auger.rig import Rig


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def call(http: httpx.AsyncClient, token: str, method: str, path: str, **kwargs: Any) -> Any:
    response = await http.request(method, path, headers=auth(token), **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def queued(rig: Rig, label: str = "a model") -> str:
    """A job that never runs, because nothing is listening at that address.

    The queue starts it, the transfer fails or hangs, and neither matters here: these
    tests are about the list and the controls, not about moving bytes.
    """
    job = rig.downloads.submit(
        label,
        "weights",
        rig.settings.home / "models" / label,
        [Item("weights.bin", "https://huggingface.co/nowhere/at/all", Digest.sha256("ab" * 32))],
    )
    return job.id


async def test_the_queue_is_listed_and_says_it_is_not_durable(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    """A restart loses the list and keeps the bytes. Better said than implied."""
    key = queued(rig)
    async with http:
        body = await call(http, token, "GET", "/downloads")
    assert [one["id"] for one in body["downloads"]] == [key]
    assert body["durable"] is False
    assert body["downloads"][0]["label"] == "a model"


async def test_pause_and_continue_change_the_state(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    key = queued(rig)
    async with http:
        paused = await call(http, token, "POST", f"/downloads/{key}/pause")
        assert paused["downloads"][0]["state"] == "paused"
        again = await call(http, token, "POST", f"/downloads/{key}/resume")
    assert again["downloads"][0]["state"] in ("queued", "running")


async def test_cancel_ends_it_and_forget_takes_it_off_the_list(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    key = queued(rig)
    async with http:
        dropped = await call(http, token, "POST", f"/downloads/{key}/cancel")
        assert dropped["downloads"][0]["state"] == "cancelled"
        cleared = await call(http, token, "POST", f"/downloads/{key}/forget")
    assert cleared["downloads"] == []


async def test_a_control_on_a_job_that_does_not_exist_says_so(
    http: httpx.AsyncClient, token: str
) -> None:
    async with http:
        response = await http.post("/downloads/d999/pause", headers=auth("test-token-not-a-secret"))
    assert response.status_code == 404


async def test_the_second_engine_reports_what_it_needs(
    http: httpx.AsyncClient, token: str, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is installed, and what it cannot do is said before anybody commits to a
    download rather than after."""
    monkeypatch.setattr(coli, "python", lambda: "")
    async with http:
        body = await call(http, token, "GET", "/engines/coli")
    assert body["installed"] == ""
    assert sorted(body["cannot_serve"]) == ["embed", "rerank"]
    assert any("Python 3" in one for one in body["problems"])
    assert body["models"], "the shortlist has to be reachable before it is installed"
    first = body["models"][0]
    assert first["disk_gb"] > 0
    assert first["uploader"], "who published a conversion is part of what it is"
    assert first["downloaded"] is False


async def test_a_model_directory_already_here_is_reported_as_fetched(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    from auger.llm.catalog import COLI_MODELS
    from auger.llm.setup import models_dir

    weights = models_dir(rig.settings.home) / COLI_MODELS[0].name
    weights.mkdir(parents=True)
    (weights / "config.json").write_text("{}", encoding="utf-8")
    async with http:
        body = await call(http, token, "GET", "/engines/coli")
    fetched = {one["name"]: one["downloaded"] for one in body["models"]}
    assert fetched[COLI_MODELS[0].name] is True
    assert list(fetched.values()).count(True) == 1


async def test_asking_it_to_embed_is_refused(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        response = await http.post(
            "/engines/coli/fetch",
            headers=auth("test-token-not-a-secret"),
            json={"repo": "someone/a-model", "job_class": "embed"},
        )
    assert response.status_code == 400
    assert "chat only" in response.json()["detail"]


async def test_a_repository_that_is_not_this_format_is_refused_before_anything_is_queued(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    """The engine reads a config and shards beside it. Queueing hundreds of gigabytes of
    something else and finding out at the end is the failure worth avoiding."""
    from auger.llm import catalog

    async def resolve(*_args: object, **_kwargs: object) -> object:
        raise catalog.CatalogError("someone/not-weights does not look like weights")

    original = catalog.resolve_repo
    catalog.resolve_repo = resolve  # type: ignore[assignment]
    try:
        async with http:
            response = await http.post(
                "/engines/coli/fetch",
                headers=auth("test-token-not-a-secret"),
                json={"repo": "someone/not-weights"},
            )
    finally:
        catalog.resolve_repo = original
    assert response.status_code == 400
    assert "does not look like weights" in response.json()["detail"]
    assert rig.downloads.jobs() == []


async def test_a_config_written_for_a_model_still_downloading_says_why_it_cannot_start(
    tmp_path: Path,
) -> None:
    """The backend is written when the download is queued, not when it lands, so the
    report is "weights not there yet" rather than a download that changed nothing."""
    from auger.config.schema import Backend
    from auger.llm.supervisor import Supervisor

    supervisor = Supervisor(tmp_path / "models", log_dir=tmp_path / "logs")
    backend = Backend(
        url="http://127.0.0.1:1345/v1", managed=True, engine="coli", model_file="a-model"
    )
    health = supervisor.start("coli-review", backend)
    assert not health.up
    assert health.reason

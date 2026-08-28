"""The reviewer's instructions, and the prompt they become."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from auger.jobs.presets import BY_KEY, PRESETS, matching
from auger.rig import Rig


async def get(http: httpx.AsyncClient, token: str, path: str) -> Any:
    response = await http.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_preset_has_its_own_key_and_words() -> None:
    keys = [preset.key for preset in PRESETS]
    assert len(keys) == len(set(keys))
    assert all(preset.name and preset.summary for preset in PRESETS)


def test_the_default_preset_adds_nothing() -> None:
    """ "As it comes" has to mean the built-in rules and nothing else."""
    assert BY_KEY["default"].instructions == ""
    assert matching("") == "default"


def test_a_users_own_words_are_not_mistaken_for_a_preset() -> None:
    assert matching("Report only what breaks the build.") == "custom"
    assert matching(BY_KEY["security"].instructions) == "security"


async def test_it_shows_the_prompt_the_model_receives(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        body = await get(http, token, "/prompt")
    assert body["preset"] == "default"
    assert body["instructions"] == ""
    # The rules and the output contract are always there, whatever the user added.
    assert "Answer with one JSON object" in body["system"]
    assert body["rules"] in body["system"]
    assert len(body["presets"]) == len(PRESETS)


async def test_a_change_can_be_read_before_it_is_saved(
    http: httpx.AsyncClient, token: str, home: Path, rig: Rig
) -> None:
    async with http:
        body = await get(http, token, "/prompt?instructions=Only%20report%20leaks.")
    assert "Only report leaks." in body["system"]
    assert body["preset"] == "custom"
    # Nothing was written. A preview is a preview.
    assert rig.config.defaults.instructions == ""


async def test_saving_a_preset_shows_up_in_the_prompt(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    async with http:
        await http.put(
            "/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "level": "defaults",
                "key": "",
                "changes": {"instructions": BY_KEY["security"].instructions},
            },
        )
        body = await get(http, token, "/prompt")
    assert body["preset"] == "security"
    assert "leaked credential" in body["system"]


async def test_the_route_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/prompt")).status_code == 401

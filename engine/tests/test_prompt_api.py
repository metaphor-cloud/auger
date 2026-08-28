"""The system prompt: the user's to read, to pick, and to rewrite."""

from __future__ import annotations

from typing import Any

import httpx

from auger.jobs.presets import BY_KEY, PRESETS, matching
from auger.jobs.prompt import SYSTEM, missing_from, system_prompt
from auger.rig import Rig


async def get(http: httpx.AsyncClient, token: str, path: str) -> Any:
    response = await http.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_preset_is_a_whole_prompt_that_can_be_read_back() -> None:
    keys = [preset.key for preset in PRESETS]
    assert len(keys) == len(set(keys))
    for preset in PRESETS:
        assert preset.name and preset.summary
        # Whatever a prompt asks for, the answer still has to be readable.
        assert missing_from(preset.system) == [], preset.key


def test_the_shipped_prompt_is_what_an_empty_setting_means() -> None:
    assert BY_KEY["default"].system.strip() == SYSTEM.strip()
    assert matching("") == "default"
    assert system_prompt("", "").strip() == SYSTEM.strip()


def test_a_users_own_prompt_is_not_mistaken_for_a_preset() -> None:
    assert matching("You review poetry.") == "custom"
    assert matching(BY_KEY["security"].system) == "security"


def test_a_prompt_that_stopped_asking_for_the_answer_is_named() -> None:
    """An edit that drops the format gives a review nothing can read."""
    assert missing_from("You review code. Say what is wrong.") == [
        "findings",
        "severity",
        "file",
        "title",
    ]
    assert missing_from("") == []


def test_the_users_prompt_replaces_the_shipped_one() -> None:
    written = system_prompt("", "You review poetry. Answer with findings, severity, file, title.")
    assert "You review poetry." in written
    assert "You review code changes and report defects." not in written


def test_a_level_can_add_a_line_without_rewriting_the_prompt() -> None:
    written = system_prompt("Treat a missing test as high.", "")
    assert SYSTEM.strip() in written
    assert "Treat a missing test as high." in written


async def test_it_shows_the_prompt_the_model_receives(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        body = await get(http, token, "/prompt")
    assert body["preset"] == "default"
    assert body["rules"].strip() == SYSTEM.strip()
    assert "Answer with one JSON object" in body["system"]
    assert body["missing"] == []
    assert len(body["presets"]) == len(PRESETS)


async def test_a_change_can_be_read_before_it_is_saved(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    async with http:
        body = await get(http, token, "/prompt?rules=You%20review%20poetry.")
    assert body["system"].startswith("You review poetry.")
    assert body["preset"] == "custom"
    assert body["missing"] == ["findings", "severity", "file", "title"]
    # Nothing was written. A preview is a preview.
    assert rig.config.defaults.system_prompt == ""


async def test_a_preset_can_be_saved_and_reaches_the_model(
    http: httpx.AsyncClient, token: str, rig: Rig
) -> None:
    async with http:
        await http.put(
            "/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "level": "defaults",
                "key": "",
                "changes": {"system_prompt": BY_KEY["security"].system},
            },
        )
        body = await get(http, token, "/prompt")
    assert body["preset"] == "security"
    assert "You review code changes for security defects" in body["system"]
    assert rig.config.defaults.system_prompt.startswith("You review code changes for")


async def test_the_route_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/prompt")).status_code == 401

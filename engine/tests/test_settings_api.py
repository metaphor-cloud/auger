"""The settings route writes the config file that the user also edits by hand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from reviewrig.config import load
from reviewrig.rig import Rig


async def call(http: httpx.AsyncClient, token: str, method: str, path: str, **kwargs: Any) -> Any:
    response = await http.request(
        method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def commented(home: Path, rig: Rig) -> Path:
    """Write a config with comments, and make the running rig read it."""
    path = home / "config.toml"
    path.write_text(
        '# my rig\n[defaults]\n# the important one\nmode = "draft"\n\n'
        '[org."github.com/acme"]\npriority = 2\n',
        encoding="utf-8",
    )
    rig.reload_config()
    return path


async def test_it_lists_every_level(http: httpx.AsyncClient, token: str, commented: Path) -> None:
    async with http:
        body = await call(http, token, "GET", "/settings")
    assert body["defaults"]["mode"] == "draft"
    assert body["levels"] == [
        {"level": "org", "key": "github.com/acme", "overrides": {"priority": 2}}
    ]


async def test_a_change_to_the_defaults_is_written(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        body = await call(
            http,
            token,
            "PUT",
            "/settings",
            json={"level": "defaults", "key": "", "changes": {"mode": "complete"}},
        )
    assert body["defaults"]["mode"] == "complete"
    assert load(commented).defaults.mode == "complete"


async def test_a_change_keeps_the_users_comments(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    """They edit this file by hand too."""
    async with http:
        await call(
            http,
            token,
            "PUT",
            "/settings",
            json={"level": "defaults", "key": "", "changes": {"priority": 1}},
        )
    text = commented.read_text(encoding="utf-8")
    assert "# my rig" in text
    assert "# the important one" in text


async def test_a_new_organisation_level_can_be_added(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        body = await call(
            http,
            token,
            "PUT",
            "/settings",
            json={
                "level": "org",
                "key": "github.com/other",
                "changes": {"mode": "off"},
            },
        )
    keys = {level["key"] for level in body["levels"]}
    assert "github.com/other" in keys
    assert load(commented).org["github.com/other"].mode == "off"


async def test_an_existing_level_keeps_the_fields_it_already_set(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        await call(
            http,
            token,
            "PUT",
            "/settings",
            json={"level": "org", "key": "github.com/acme", "changes": {"mode": "off"}},
        )
    overrides = load(commented).org["github.com/acme"]
    assert overrides.mode == "off"
    assert overrides.priority == 2


async def test_a_level_with_no_key_is_refused(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        response = await http.put(
            "/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"level": "org", "key": "", "changes": {"mode": "off"}},
        )
    assert response.status_code == 400


async def test_a_bad_value_is_refused_and_changes_nothing(
    http: httpx.AsyncClient, token: str, commented: Path, rig: Rig
) -> None:
    async with http:
        response = await http.put(
            "/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={"level": "org", "key": "github.com/acme", "changes": {"mode": "sideways"}},
        )
    assert response.status_code == 422 or response.status_code == 400
    assert load(commented).org["github.com/acme"].mode is None


async def test_the_forges_are_listed_with_their_state(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        body = await call(http, token, "GET", "/forges")
    names = {forge["name"] for forge in body["forges"]}
    assert names == {"github", "gitlab"}
    assert all(forge["enabled"] is False for forge in body["forges"])


async def test_the_routes_need_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/settings")).status_code == 401
        assert (await http.get("/forges")).status_code == 401
        assert (await http.put("/settings", json={})).status_code == 401


# --- exclusions and the call graph -----------------------------------------------------


async def test_a_repository_can_be_excluded_and_brought_back(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        body = await call(
            http,
            token,
            "PUT",
            "/settings/exclude",
            json={"pattern": "~/git/scratch", "remove": False},
        )
        assert body["exclude"] == ["~/git/scratch"]
        assert load(commented).exclude == ["~/git/scratch"]
        body = await call(
            http,
            token,
            "PUT",
            "/settings/exclude",
            json={"pattern": "~/git/scratch", "remove": True},
        )
    assert body["exclude"] == []


async def test_the_same_exclusion_is_not_added_twice(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        for _ in range(3):
            body = await call(http, token, "PUT", "/settings/exclude", json={"pattern": "~/git/x"})
    assert body["exclude"] == ["~/git/x"]


async def test_an_empty_exclusion_is_refused(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        response = await http.put(
            "/settings/exclude",
            headers={"Authorization": f"Bearer {token}"},
            json={"pattern": "   "},
        )
    assert response.status_code == 400


async def test_an_excluded_repository_is_never_reviewed(
    http: httpx.AsyncClient, token: str, rig: Rig, commented: Path, tmp_path: Path
) -> None:
    """It stays listed, so the user can see it was excluded rather than lost."""
    from reviewrig.models import Remote, Repository

    repository = Repository(
        path=tmp_path / "dropped", remote=Remote("github.com", "acme", "dropped")
    )
    assert rig.policy_for(repository).enabled is True
    rig.change_exclusion(str(tmp_path / "dropped"), remove=False)
    policy = rig.policy_for(repository)
    assert policy.enabled is False
    assert policy.mode == "off"


async def test_the_call_graph_is_a_global_switch(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    async with http:
        assert (await call(http, token, "GET", "/settings"))["codegraph"] is False
        body = await call(http, token, "PUT", "/settings/codegraph", json={"enabled": True})
    assert body["codegraph"] is True
    assert load(commented).codegraph.enabled is True


async def test_the_settings_say_whether_codegraph_is_installed(
    http: httpx.AsyncClient, token: str, commented: Path
) -> None:
    """Turning it on without the tool would do nothing and say nothing."""
    async with http:
        body = await call(http, token, "GET", "/settings")
    assert isinstance(body["codegraph_available"], bool)

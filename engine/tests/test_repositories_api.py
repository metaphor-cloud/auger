from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from auger.rig import Rig
from tests.helpers import make_repo


@pytest.fixture
def tree(tmp_path: Path, home: Path) -> Path:
    """Two repositories in one root, with a policy that differs between them."""
    tree = tmp_path / "tree"
    make_repo(tree / "alpha", remote="git@github.com:acme/alpha.git")
    make_repo(tree / "beta", remote="git@github.com:other/beta.git")
    (home / "config.toml").write_text(
        f"""
[[roots]]
path = "{tree}"

[defaults]
mode = "draft"
priority = 5

[org."github.com/acme"]
mode = "complete"

[repo."{tree}/beta"]
enabled = false
priority = 9
""",
        encoding="utf-8",
    )
    return tree


async def get(http: httpx.AsyncClient, token: str, path: str) -> Any:
    response = await http.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


async def post(http: httpx.AsyncClient, token: str, path: str) -> Any:
    response = await http.post(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_scan_returns_both_repositories(
    http: httpx.AsyncClient, token: str, tree: Path
) -> None:
    async with http:
        body = await post(http, token, "/scan")
    names = [repository["name"] for repository in body["repositories"]]
    assert names == ["alpha", "beta"]
    assert body["enabled"] == 1


async def test_the_policy_reaches_the_ui(http: httpx.AsyncClient, token: str, tree: Path) -> None:
    async with http:
        body = await post(http, token, "/scan")
    by_name = {item["name"]: item for item in body["repositories"]}
    assert by_name["alpha"]["policy"]["mode"] == "complete"
    assert by_name["alpha"]["org_key"] == "github.com/acme"
    assert by_name["beta"]["policy"]["enabled"] is False
    assert by_name["beta"]["policy"]["priority"] == 9


async def test_the_list_survives_a_restart(
    http: httpx.AsyncClient, token: str, tree: Path, rig: Rig
) -> None:
    """The list route reads the store, so the UI shows repositories before the first walk."""
    async with http:
        await post(http, token, "/scan")
        body = await get(http, token, "/repositories")
    assert [item["name"] for item in body["repositories"]] == ["alpha", "beta"]


async def test_a_scan_rereads_the_config(
    http: httpx.AsyncClient, token: str, tree: Path, home: Path
) -> None:
    async with http:
        await post(http, token, "/scan")
        (home / "config.toml").write_text(
            f'[[roots]]\npath = "{tree}"\n\n[defaults]\nenabled = false\n', encoding="utf-8"
        )
        body = await post(http, token, "/scan")
    assert body["enabled"] == 0


async def test_the_routes_need_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/repositories")).status_code == 401
        assert (await http.post("/scan")).status_code == 401

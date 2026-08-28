from __future__ import annotations

from typing import Any

import httpx


async def system(http: httpx.AsyncClient, token: str) -> Any:
    response = await http.get("/system", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_it_names_the_sandbox_backend(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        body = await system(http, token)
    assert body["sandbox"]["backend"] in {"apple-container", "podman", "docker", "seatbelt"}


async def test_a_degraded_sandbox_carries_a_warning(http: httpx.AsyncClient, token: str) -> None:
    """The user must know when analysis dropped to Seatbelt."""
    async with http:
        body = await system(http, token)
    sandbox = body["sandbox"]
    assert sandbox["degraded"] is (sandbox["backend"] == "seatbelt")
    assert (sandbox["warning"] is not None) is sandbox["degraded"]


async def test_the_allowlist_holds_the_local_model_backends(
    http: httpx.AsyncClient, token: str
) -> None:
    """A backend the rig is configured to use must be reachable, and nothing else."""
    async with http:
        body = await system(http, token)
    allowed = body["egress"]["allowed"]
    assert "127.0.0.1:8080" in allowed
    assert all(
        destination.startswith(("127.0.0.1", "localhost", "::1", "0.0.0.0"))
        for destination in allowed
    )
    assert body["egress"]["refused_requests"] == 0


async def test_it_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/system")).status_code == 401


async def test_a_refused_config_reaches_the_ui(
    http: httpx.AsyncClient, token: str, home: object
) -> None:
    """A rig quietly running on defaults would review the wrong repositories."""
    from pathlib import Path

    Path(str(home), "config.toml").write_text(
        "[schedule]\naudit_poll_seconds = 20\n", encoding="utf-8"
    )
    async with http:
        await http.post("/scan", headers={"Authorization": f"Bearer {token}"})
        body = await system(http, token)
    assert body["config_error"] is not None
    assert "audit_poll_seconds" in body["config_error"]

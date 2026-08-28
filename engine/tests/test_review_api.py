from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from auger.rig import Rig
from auger.store.findings import Finding, record
from tests.helpers import git_commit, git_init


@pytest.fixture
def tree(tmp_path: Path, home: Path) -> Path:
    tree = tmp_path / "tree"
    path = git_init(tree / "alpha", remote="git@github.com:acme/alpha.git")
    git_commit(path, {"a.py": "x = 1\n"}, "one")
    (home / "config.toml").write_text(
        f'[[roots]]\npath = "{tree}"\n\n[schedule]\npoll_seconds = 3600\n', encoding="utf-8"
    )
    return tree


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def call(http: httpx.AsyncClient, token: str, method: str, path: str, **kwargs: Any) -> Any:
    response = await http.request(method, path, headers=auth(token), **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def seed(rig: Rig, repo_path: Path) -> Finding:
    finding = Finding(
        repo_path=str(repo_path),
        source="model",
        severity="high",
        title="File handle is never closed",
        detail="it leaks",
        file="a.py",
        line=2,
    )
    record(rig.store, [finding])
    return finding


async def test_findings_are_listed_with_their_counts(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    seed(rig, tree / "alpha")
    async with http:
        body = await call(http, token, "GET", "/findings")
    assert body["findings"][0]["title"] == "File handle is never closed"
    assert body["counts"]["high"] == 1
    assert body["counts"]["total"] == 1


async def test_a_finding_can_be_suppressed_and_brought_back(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    finding = seed(rig, tree / "alpha")
    async with http:
        body = await call(
            http,
            token,
            "POST",
            "/findings/status",
            json={"fingerprints": [finding.fingerprint], "status": "suppressed"},
        )
        assert body["counts"]["total"] == 0
        body = await call(
            http,
            token,
            "POST",
            "/findings/status",
            json={"fingerprints": [finding.fingerprint], "status": "open"},
        )
    assert body["counts"]["total"] == 1


async def test_findings_can_be_read_for_one_repository(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    seed(rig, tree / "alpha")
    async with http:
        mine = await call(http, token, "GET", f"/findings?repo={tree / 'alpha'}")
        other = await call(http, token, "GET", "/findings?repo=/somewhere/else")
    assert len(mine["findings"]) == 1
    assert other["findings"] == []


async def test_a_review_can_be_asked_for_by_hand(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    async with http:
        await call(http, token, "POST", "/scan")
        body = await call(http, token, "POST", "/review", json={"path": str(tree / "alpha")})
    assert body["pending"] >= 1


async def test_a_review_of_a_path_that_is_not_a_repository_is_refused(
    http: httpx.AsyncClient, token: str, tree: Path
) -> None:
    async with http:
        response = await http.post("/review", headers=auth(token), json={"path": "/not/a/repo"})
    assert response.status_code == 404


async def test_the_queue_can_be_paused_and_resumed(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    from auger.llm import Health

    # Play is refused when no model answers, so this one does.
    rig.health["local-review"] = Health(
        name="local-review", url="http://127.0.0.1:1337/v1", up=True
    )
    async with http:
        assert (await call(http, token, "POST", "/queue/pause"))["paused"] is True
        assert (await call(http, token, "GET", "/queue"))["paused"] is True
        assert (await call(http, token, "POST", "/queue/resume"))["paused"] is False


async def test_the_run_log_is_readable(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    from auger.store.runs import record_skip

    record_skip(rig.store, tree / "alpha", "diff_review", "agent_running", "claude(1)")
    async with http:
        body = await call(http, token, "GET", "/runs")
    assert body["runs"][0]["status"] == "skipped"
    assert body["runs"][0]["reason"] == "agent_running"


async def test_every_route_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        for method, path in [
            ("GET", "/findings"),
            ("GET", "/runs"),
            ("GET", "/queue"),
            ("POST", "/review"),
            ("POST", "/queue/pause"),
        ]:
            assert (await http.request(method, path)).status_code == 401


async def test_a_repeat_skip_shares_one_row(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    """A repository that stays busy must not bury the rest of the log."""
    from auger.store.runs import record_skip

    for _ in range(5):
        record_skip(rig.store, tree / "alpha", "diff_review", "agent_running", "claude(1)")
    record_skip(rig.store, tree / "alpha", "diff_review", "recent_write", "2s ago")
    async with http:
        body = await call(http, token, "GET", "/runs")
    assert [(run["reason"], run["attempts"]) for run in body["runs"]] == [
        ("recent_write", 1),
        ("agent_running", 5),
    ]


async def test_a_security_scan_can_be_asked_for_by_hand(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    async with http:
        await call(http, token, "POST", "/scan")
        body = await call(http, token, "POST", "/scan/security", json={"path": str(tree / "alpha")})
    assert body["pending"] >= 1


async def test_a_dismissed_finding_can_still_be_asked_for(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    from auger.store.findings import set_triage

    finding = seed(rig, tree / "alpha")
    set_triage(rig.store, finding.fingerprint, "false", "not affected")
    async with http:
        hidden = await call(http, token, "GET", "/findings")
        shown = await call(http, token, "GET", "/findings?include_dismissed=true")
    assert hidden["findings"] == []
    assert len(shown["findings"]) == 1


async def test_the_queue_says_when_it_has_not_started_yet(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    """The first walk takes seconds. Until the workers exist the queue is neither
    running nor stopped, and the window must not claim it is reviewing."""
    async with http:
        before = await call(http, token, "GET", "/queue")
        assert before["ready"] is False

        await rig.start_background()
        after = await call(http, token, "GET", "/queue")
    assert after["ready"] is True
    assert after["paused"] is True

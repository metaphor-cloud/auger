"""The landing page reads once, so every number on it is from the same moment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from reviewrig.rig import Rig
from reviewrig.store.findings import Finding, record, set_status, set_triage
from reviewrig.store.runs import Run, finish, record_skip, start
from tests.helpers import git_commit, git_init


async def get(http: httpx.AsyncClient, token: str, path: str) -> Any:
    response = await http.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def tree(tmp_path: Path, home: Path) -> Path:
    tree = tmp_path / "tree"
    for name in ("alpha", "beta", "dropped"):
        path = git_init(tree / name, remote=f"git@github.com:acme/{name}.git")
        git_commit(path, {"a.py": "x = 1\n"}, "one")
    # `exclude` is a top level key, so it must come before any table. Written after
    # `[[roots]]` it would land inside that table and become a root's own exclusion.
    (home / "config.toml").write_text(
        f'exclude = ["{tree}/dropped"]\n\n[[roots]]\npath = "{tree}"\n', encoding="utf-8"
    )
    return tree


def finding(repo: Path, title: str, severity: str = "high") -> Finding:
    return Finding(
        repo_path=str(repo),
        source="model",
        severity=severity,  # type: ignore[arg-type]
        title=title,
        detail="it leaks",
        file="a.py",
        line=2,
    )


async def test_it_counts_the_open_findings_by_severity(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    record(
        rig.store,
        [
            finding(tree / "alpha", "one", "critical"),
            finding(tree / "alpha", "two", "high"),
            finding(tree / "beta", "three", "low"),
        ],
    )
    async with http:
        body = await get(http, token, "/dashboard")
    assert body["findings"]["critical"] == 1
    assert body["findings"]["total"] == 3


async def test_a_suppressed_or_dismissed_finding_is_counted_apart(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    record(rig.store, [finding(tree / "alpha", "one"), finding(tree / "alpha", "two")])
    from reviewrig.store.findings import list_findings

    rows = list_findings(rig.store)
    set_status(rig.store, [rows[0].fingerprint], "suppressed")
    set_triage(rig.store, rows[1].fingerprint, "false", "not affected")
    async with http:
        body = await get(http, token, "/dashboard")
    assert body["findings"]["total"] == 0
    assert body["suppressed"] == 1
    assert body["dismissed"] == 1


async def test_it_names_the_repositories_that_need_attention_first(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    record(
        rig.store,
        [
            finding(tree / "alpha", "one", "low"),
            finding(tree / "beta", "two", "critical"),
        ],
    )
    async with http:
        body = await get(http, token, "/dashboard")
    assert [item["name"] for item in body["busiest"]] == ["beta", "alpha"]
    assert body["busiest"][0]["worst_severity"] == "critical"


async def test_it_counts_the_runs_and_what_they_cost(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    run: Run = start(rig.store, tree / "alpha", "diff_review", None, "abc")
    run.status = "ok"
    run.prompt_tokens = 900
    run.completion_tokens = 120
    finish(rig.store, run)
    async with http:
        body = await get(http, token, "/dashboard")
    assert body["runs_by_status"]["ok"] == 1
    assert body["runs_today"] == 1
    assert body["prompt_tokens"] == 900
    assert body["completion_tokens"] == 120
    assert body["last_run_at"]


async def test_it_says_why_repositories_were_skipped(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    """A repository that is never reviewed must be visible on the landing page."""
    record_skip(rig.store, tree / "alpha", "diff_review", "agent_running", "claude(1)")
    record_skip(rig.store, tree / "beta", "diff_review", "agent_running", "claude(2)")
    record_skip(rig.store, tree / "beta", "diff_review", "recent_write", "2s ago")
    async with http:
        body = await get(http, token, "/dashboard")
    assert body["skipped_reasons"]["agent_running"] == 2
    assert body["skipped_reasons"]["recent_write"] == 1


async def test_it_counts_the_excluded_repositories(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    async with http:
        await http.post("/scan", headers={"Authorization": f"Bearer {token}"})
        body = await get(http, token, "/dashboard")
    assert body["repositories"] == 3
    assert body["excluded"] == 1
    assert body["enabled"] == 2


async def test_it_reports_what_needs_the_user(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    """A managed model that is not answering is the reason nothing gets reviewed."""
    async with http:
        body = await get(http, token, "/dashboard")
    assert any("Set up" in warning for warning in body["warnings"])


async def test_a_refused_config_is_the_first_warning(
    http: httpx.AsyncClient, token: str, rig: Rig, home: Path
) -> None:
    (home / "config.toml").write_text("[schedule]\npoll_seconds = 1\n", encoding="utf-8")
    rig.reload_config()
    async with http:
        body = await get(http, token, "/dashboard")
    assert "poll_seconds" in body["warnings"][0]


async def test_it_reports_the_queue_and_the_sandbox(
    http: httpx.AsyncClient, token: str, rig: Rig, tree: Path
) -> None:
    async with http:
        await http.post("/queue/pause", headers={"Authorization": f"Bearer {token}"})
        body = await get(http, token, "/dashboard")
    assert body["paused"] is True
    assert body["sandbox"]["backend"]
    assert body["codegraph"] is False


async def test_it_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/dashboard")).status_code == 401

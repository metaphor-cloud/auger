"""The work tracker: the store behind it, and the server an agent talks to."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from auger.store.db import Store
from auger.store.findings import (
    ACTIVE,
    Finding,
    list_findings,
    record,
    record_one,
    search_findings,
    set_status,
)
from auger.store.notes import add_note, note_counts, notes_for
from auger.tracker.repo import repository_for

REPO = "/tmp/repo"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    store = Store.open(tmp_path)
    yield store
    store.close()


def task(title: str, detail: str = "", **extra: Any) -> Finding:
    """What the tracker writes when an agent records work."""
    return Finding(
        repo_path=REPO,
        source="agent",
        severity="medium",
        category=extra.pop("category", "task"),
        title=title,
        detail=detail,
        file=extra.pop("file", ""),
        **extra,
    )


# --- the store ---------------------------------------------------------------------


def test_the_same_work_recorded_twice_is_one_item(store: Store) -> None:
    """This is what stops an agent from doing the same work again."""
    first, existed_first = record_one(store, task("add a retry to the fetch helper"))
    second, existed_second = record_one(store, task("add a retry to the fetch helper"))

    assert existed_first is False
    assert existed_second is True
    assert first.fingerprint == second.fingerprint
    assert second.times_seen == 2
    assert len(list_findings(store, REPO)) == 1


def test_a_note_says_what_happened_last_time(store: Store) -> None:
    stored, _ = record_one(store, task("add a retry to the fetch helper"))
    add_note(store, stored.fingerprint, "tried it, the test server has no backoff")
    add_note(store, stored.fingerprint, "waiting on the upstream fix")

    notes = notes_for(store, stored.fingerprint)
    assert [note.text for note in notes] == [
        "tried it, the test server has no backoff",
        "waiting on the upstream fix",
    ]
    assert note_counts(store, [stored.fingerprint]) == {stored.fingerprint: 2}


def test_a_note_on_an_item_that_does_not_exist_is_refused(store: Store) -> None:
    with pytest.raises(ValueError):
        add_note(store, "0" * 32, "a note")


def test_search_finds_an_item_by_a_word_in_its_detail(store: Store) -> None:
    record_one(store, task("tidy the parser", "the retry loop in fetch.py never backs off"))
    record_one(store, task("write the release notes"))

    found = search_findings(store, "backs retry", REPO)
    assert [one.title for one in found] == ["tidy the parser"]


def test_search_finds_what_a_review_found(store: Store) -> None:
    """One list, whoever wrote it. A task and a finding are the same row."""
    record(
        store,
        [
            Finding(
                repo_path=REPO,
                source="model",
                severity="high",
                title="the token is written to the log",
                detail="log.info prints the whole header",
                file="api.py",
                line=12,
            )
        ],
    )
    found = search_findings(store, "token log", REPO)
    assert len(found) == 1
    assert found[0].source == "model"


def test_a_search_that_matches_nothing_is_empty_not_an_error(store: Store) -> None:
    record_one(store, task("tidy the parser"))
    assert search_findings(store, "elephant giraffe", REPO) == []
    assert search_findings(store, "!!! ??", REPO) == []


def test_an_edited_item_is_searchable_by_its_new_words(store: Store) -> None:
    """A repeat updates the row, so the index has to follow the update."""
    stored, _ = record_one(store, task("tidy the parser", "the first detail"))
    record_one(store, task("tidy the parser", "now it mentions unicode"))

    assert [one.fingerprint for one in search_findings(store, "unicode", REPO)] == [
        stored.fingerprint
    ]
    assert search_findings(store, "first detail", REPO) == []


def test_work_in_flight_is_still_unfinished(store: Store) -> None:
    stored, _ = record_one(store, task("tidy the parser"))
    set_status(store, [stored.fingerprint], "doing")

    assert [one.status for one in list_findings(store, REPO, ACTIVE)] == ["doing"]
    assert list_findings(store, REPO, ["open"]) == []


def test_a_closed_item_leaves_the_list(store: Store) -> None:
    stored, _ = record_one(store, task("tidy the parser"))
    set_status(store, [stored.fingerprint], "resolved")
    assert list_findings(store, REPO) == []


def test_a_repository_is_found_from_a_directory_inside_it(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "src" / "deep").mkdir(parents=True)

    assert repository_for(tmp_path / "repo" / "src" / "deep") == tmp_path / "repo"
    assert repository_for(tmp_path) is None


def test_a_worktree_is_a_repository_too(tmp_path: Path) -> None:
    """A worktree and a submodule hold `.git` as a file, not a directory."""
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/tree\n")
    assert repository_for(tmp_path / "tree") == tmp_path / "tree"


# --- the server, over a real stdio connection --------------------------------------


async def call(client: Any, name: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    text = "".join(getattr(block, "text", "") for block in result.content)
    return dict(json.loads(text))


def tracker(home: Path, repository: Path) -> Any:
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    return stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "auger.tracker",
                "--repo",
                str(repository),
                "--home",
                str(home),
            ],
        )
    )


def checkout(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    return repository


@pytest.mark.timeout(60)
async def test_an_agent_can_record_search_and_close_work(tmp_path: Path) -> None:
    """The whole point, through a real MCP client over stdio."""
    from mcp import ClientSession

    repository = checkout(tmp_path)
    async with (
        tracker(tmp_path / "home", repository) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        await client.initialize()
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {
            "search",
            "record",
            "note",
            "set_state",
            "list_open",
            "overview",
            "everywhere",
            "runs",
        }

        first = await call(
            client,
            "record",
            title="add a retry to the fetch helper",
            detail="it gives up on the first timeout",
        )
        assert first["existed"] is False
        item_id = first["item"]["id"]

        again = await call(client, "record", title="add a retry to the fetch helper")
        assert again["existed"] is True
        assert again["item"]["id"] == item_id

        await call(client, "set_state", id=item_id, state="doing")
        noted = await call(client, "note", id=item_id, text="the server has no backoff")
        assert noted["item"]["journal"][0]["text"] == "the server has no backoff"
        assert noted["item"]["state"] == "doing"

        found = await call(client, "search", query="retry timeout")
        assert [one["id"] for one in found["items"]] == [item_id]

        outstanding = await call(client, "list_open")
        assert len(outstanding["items"]) == 1

        await call(client, "set_state", id=item_id, state="done")
        assert await call(client, "list_open") == {
            "repository": str(repository),
            "items": [],
        }


@pytest.mark.timeout(60)
async def test_a_second_session_reads_what_the_first_one_wrote(tmp_path: Path) -> None:
    """A new process, the same repository, and the work is still there."""
    from mcp import ClientSession

    repository = checkout(tmp_path)
    home = tmp_path / "home"

    async with (
        tracker(home, repository) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        await client.initialize()
        written = await call(client, "record", title="rewrite the walk")
        await call(client, "note", id=written["item"]["id"], text="half done")

    async with (
        tracker(home, repository) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        await client.initialize()
        found = await call(client, "search", query="rewrite walk")
    assert found["items"][0]["title"] == "rewrite the walk"
    assert found["items"][0]["notes"] == 1


@pytest.mark.timeout(60)
async def test_a_state_the_tracker_does_not_know_is_refused(tmp_path: Path) -> None:
    from mcp import ClientSession

    repository = checkout(tmp_path)
    async with (
        tracker(tmp_path / "home", repository) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        await client.initialize()
        written = await call(client, "record", title="rewrite the walk")
        result = await client.call_tool(
            "set_state", {"id": written["item"]["id"], "state": "in progress"}
        )
    assert result.is_error
    assert "doing" in "".join(getattr(block, "text", "") for block in result.content)


# --- the routes --------------------------------------------------------------------
#
# The window reads and annotates. It never writes an item: an item comes from a review
# or from an agent through the tracker, so these seed the store the way those do.


async def post(http: Any, token: str, path: str, body: dict[str, Any]) -> Any:
    response = await http.post(path, headers={"Authorization": f"Bearer {token}"}, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_window_cannot_record_an_item(http: Any, token: str) -> None:
    """The tracker is written by agents, not by hand, so the route does not exist."""
    async with http:
        response = await http.post(
            "/findings",
            headers={"Authorization": f"Bearer {token}"},
            json={"repo_path": REPO, "title": "drop the old walk"},
        )
    assert response.status_code == 405


async def test_a_note_is_appended_to_an_item_an_agent_wrote(
    rig: Any, http: Any, token: str
) -> None:
    stored, _ = record_one(rig.store, task("drop the old walk", "it reads twice"))
    async with http:
        path = f"/findings/{stored.fingerprint}/notes"
        notes = await post(http, token, path, {"text": "started it"})
        assert [note["text"] for note in notes["notes"]] == ["started it"]

        read = await http.get(
            f"/findings/{stored.fingerprint}/notes", headers={"Authorization": f"Bearer {token}"}
        )
    assert read.json()["notes"][0]["author"] == "person"


async def test_the_findings_route_searches(rig: Any, http: Any, token: str) -> None:
    record_one(rig.store, task("drop the old walk", "it reads twice"))
    record_one(rig.store, task("write the notes"))
    async with http:
        response = await http.get(
            "/findings?query=reads twice", headers={"Authorization": f"Bearer {token}"}
        )
    assert [one["title"] for one in response.json()["findings"]] == ["drop the old walk"]


async def test_an_item_can_be_moved_to_doing_and_stays_in_the_list(
    rig: Any, http: Any, token: str
) -> None:
    stored, _ = record_one(rig.store, task("drop the old walk"))
    async with http:
        body = await post(
            http,
            token,
            "/findings/status",
            {"fingerprints": [stored.fingerprint], "status": "doing"},
        )
    assert [one["status"] for one in body["findings"]] == ["doing"]
    assert body["counts"]["total"] == 1


async def test_a_note_on_an_unknown_item_is_refused(http: Any, token: str) -> None:
    async with http:
        response = await http.post(
            f"/findings/{'0' * 32}/notes",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "a note"},
        )
    assert response.status_code == 400


async def test_a_finding_is_new_until_it_is_opened(rig: Any, http: Any, token: str) -> None:
    """The map flags what the user has not read. Reading it clears the flag."""
    stored, _ = record_one(rig.store, task("drop the old walk"))
    assert stored.opened_at is None
    assert stored.category == "task"

    async with http:
        body = await post(http, token, f"/findings/{stored.fingerprint}/opened", {})
    assert body["findings"][0]["opened_at"] is not None


async def test_a_category_survives_a_repeat(store: Store) -> None:
    """A re-review may change the kind, and that must not split the row."""
    first, _ = record_one(store, task("tidy the parser"))
    changed = task("tidy the parser")
    changed.category = "security"
    second, existed = record_one(store, changed)

    assert existed is True
    assert first.fingerprint == second.fingerprint
    assert second.category == "security"


@pytest.mark.timeout(60)
async def test_the_whole_machine_can_be_read_from_inside_one_repository(tmp_path: Path) -> None:
    """Whether the reviews are worth having is not a question one repository answers,
    so the read-only tools ignore the repository the agent is standing in."""
    from mcp import ClientSession

    home = tmp_path / "home"
    store = Store.open(home)
    record(
        store,
        [
            Finding(
                fingerprint="a",
                repo_path="/somewhere/else",
                source="diff_review",
                severity="high",
                title="a leak in the other repository",
                detail="not the one the agent is in",
                file="x.py",
                line=1,
            )
        ],
    )
    store.close()

    async with (
        tracker(home, checkout(tmp_path)) as streams,
        ClientSession(streams[0], streams[1]) as client,
    ):
        await client.initialize()

        mine = await call(client, "list_open")
        assert mine["items"] == [], "this repository has nothing"

        everywhere = await call(client, "everywhere")
        assert [one["repository"] for one in everywhere["items"]] == ["/somewhere/else"]

        overview = await call(client, "overview")
        assert overview["findings"]["by_status"]["open"] == 1
        assert overview["repositories"][0]["repo_path"] == "/somewhere/else"
        assert "runs" in overview

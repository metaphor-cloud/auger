"""The work tracker, as an MCP server.

An agent that works in a repository has no memory between sessions. It repeats work it
already did, and it cannot tell what it left half finished. This gives it a place to
look and a place to write, scoped to the repository it stands in.

The window is not the point. The agent is. A tracker that only a window can reach
becomes a second inbox that nobody opens.

The server holds no network and no token. It opens the rig's database directly, so it
works when the application is closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from reviewrig.store.db import Store
from reviewrig.store.findings import (
    ACTIVE,
    SEVERITY_ORDER,
    Finding,
    Status,
    get_finding,
    list_findings,
    record_one,
    search_findings,
    set_status,
)
from reviewrig.store.notes import Note, add_note, note_counts, notes_for

#: What an agent calls a state, and what the store calls it. `resolved` and
#: `suppressed` are the review's words. `done` and `dropped` are the work's words, and
#: an agent reads its own tools better than it reads someone else's.
STATES: dict[str, Status] = {
    "open": "open",
    "doing": "doing",
    "done": "resolved",
    "dropped": "suppressed",
}
BACK: dict[str, str] = {value: key for key, value in STATES.items()}

#: A refusal the caller can act on. The SDK keeps the text of a `ToolError` and hides
#: the text of anything else, and an agent that cannot read the reason cannot correct
#: itself.
Refused = ToolError

#: The source an item carries when an agent wrote it. It sits beside `model` and
#: `semgrep`, so the window shows who said it.
AGENT_SOURCE = "agent"
#: The source an item carries when a person wrote it in the window.
PERSON_SOURCE = "person"

INSTRUCTIONS = """\
Work items for this repository, shared with the reviewer that runs in the background.

Search before you start. If the work is already recorded, you may have done it before,
and the notes on the item say what happened last time.

Record what you set out to do, move it to `doing` while you work, and add a note when
you learn something that the next session would want. Close it with `done` when it
works, or `dropped` when you decide not to do it.

The reviewer writes here too. An item whose source is `model` or `semgrep` came from a
review of this code, not from a person.
"""


def item(finding: Finding, notes: list[Note] | None = None, note_count: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": finding.fingerprint,
        "title": finding.title,
        "detail": finding.detail,
        "state": BACK.get(finding.status, finding.status),
        "severity": finding.severity,
        "source": finding.source,
        "file": finding.file,
        "line": finding.line,
        "first_seen_at": finding.first_seen_at,
        "last_seen_at": finding.last_seen_at,
        "times_seen": finding.times_seen,
        "notes": note_count if notes is None else len(notes),
    }
    if finding.suggestion:
        body["suggestion"] = finding.suggestion
    if notes is not None:
        body["journal"] = [
            {"at": note.written_at, "author": note.author, "text": note.text} for note in notes
        ]
    return body


def _with_counts(store: Store, findings: list[Finding]) -> list[dict[str, Any]]:
    counts = note_counts(store, [finding.fingerprint for finding in findings])
    return [item(finding, note_count=counts.get(finding.fingerprint, 0)) for finding in findings]


def build(store: Store, repository: Path, version: str = "") -> MCPServer:
    """One server for one repository."""
    repo = str(repository)
    server: MCPServer = MCPServer(
        name="reviewrig",
        title="reviewrig work tracker",
        instructions=INSTRUCTIONS,
        version=version,
        log_level="WARNING",
    )

    @server.tool(
        description=(
            "Search the work items of this repository by words. Do this before you "
            "start something, to find out whether it is already recorded."
        )
    )
    def search(query: str, limit: int = 10, include_closed: bool = False) -> dict[str, Any]:
        statuses = () if include_closed else ACTIVE
        found = search_findings(store, query, repo, statuses, limit)
        return {"repository": repo, "items": _with_counts(store, found)}

    @server.tool(
        description=(
            "Record one piece of work. The same work recorded twice returns the item "
            "that is already there, with its journal, rather than a second copy."
        )
    )
    def record(
        title: str,
        detail: str = "",
        file: str = "",
        line: int | None = None,
        severity: str = "medium",
    ) -> dict[str, Any]:
        if not title.strip():
            raise Refused("an item needs a title")
        if severity not in SEVERITY_ORDER:
            raise Refused(f"severity must be one of {', '.join(SEVERITY_ORDER)}")
        stored, existed = record_one(
            store,
            Finding(
                repo_path=repo,
                source=AGENT_SOURCE,
                severity=severity,  # type: ignore[arg-type]
                category="task",
                title=title.strip(),
                detail=detail.strip(),
                file=file.strip(),
                line=line,
            ),
        )
        notes = notes_for(store, stored.fingerprint) if existed else []
        return {"existed": existed, "item": item(stored, notes)}

    @server.tool(
        description=(
            "Add a note to an item. Write what you did, what you found, or why you "
            "stopped. The next session reads this."
        )
    )
    def note(id: str, text: str) -> dict[str, Any]:  # noqa: A002
        try:
            add_note(store, id, text, AGENT_SOURCE)
        except ValueError as error:
            raise Refused(str(error)) from error
        found = get_finding(store, id)
        if found is None:
            raise Refused(f"no item with id {id}")
        return {"item": item(found, notes_for(store, id))}

    @server.tool(
        description=(
            "Move an item to open, doing, done, or dropped. Move it to doing before "
            "you start, so a second session can see that you are on it."
        )
    )
    def set_state(id: str, state: str) -> dict[str, Any]:  # noqa: A002
        wanted = STATES.get(state)
        if wanted is None:
            raise Refused(f"state must be one of {', '.join(STATES)}")
        if set_status(store, [id], wanted) == 0:
            raise Refused(f"no item with id {id}")
        found = get_finding(store, id)
        if found is None:
            raise Refused(f"no item with id {id}")
        return {"item": item(found, notes_for(store, id))}

    @server.tool(
        description=(
            "Every unfinished item for this repository, most severe first. This is "
            "what is outstanding, including what a review found."
        )
    )
    def list_open(limit: int = 20) -> dict[str, Any]:
        found = list_findings(store, repo, ACTIVE, limit)
        return {"repository": repo, "items": _with_counts(store, found)}

    return server

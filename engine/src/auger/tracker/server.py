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

from auger.store.db import Store
from auger.store.findings import (
    ACTIVE,
    SEVERITY_ORDER,
    Finding,
    Status,
    counts,
    get_finding,
    list_findings,
    record_one,
    search_findings,
    set_status,
)
from auger.store.notes import Note, add_note, note_counts, notes_for
from auger.store.runs import list_runs

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
#: The author a journal note carries when a person wrote it in the window. No item is
#: written by hand: items come from a review or from an agent through this server.
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


def _rows(store: Store, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in store.query(sql, [])]


def _tally(store: Store, sql: str) -> dict[str, Any]:
    """A `k, n` query as a plain mapping. Read across, not row by row."""
    return {str(row["k"]): row["n"] for row in store.query(sql, [])}


def item(finding: Finding, notes: list[Note] | None = None, note_count: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": finding.fingerprint,
        # An item carries where it lives, because a list can hold several repositories
        # and an agent reads these one item at a time.
        "repository": finding.repo_path,
        "title": finding.title,
        "detail": finding.detail,
        "state": BACK.get(finding.status, finding.status),
        "severity": finding.severity,
        "category": finding.category,
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
        name="auger",
        title="auger work tracker",
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

    # --- across every repository, read only -------------------------------------------
    #
    # The tools above are for an agent working in one repository. These are for judging
    # whether the reviews are worth having at all, which is not a question one
    # repository can answer.

    @server.tool(
        description=(
            "The state of every repository at once: how much work is open, what has "
            "run, what failed and why, and how much of it a second model threw out. "
            "Start here when the question is whether the reviews are any good."
        )
    )
    def overview() -> dict[str, Any]:
        return {
            "findings": {
                "by_status": _tally(
                    store, "SELECT status AS k, COUNT(*) n FROM findings GROUP BY 1"
                ),
                "by_category": _tally(
                    store,
                    "SELECT category AS k, COUNT(*) n FROM findings"
                    " WHERE status IN ('open','doing') GROUP BY 1",
                ),
                "by_severity": counts(store),
                "judged": _tally(
                    store,
                    "SELECT COALESCE(triage,'unjudged') AS k, COUNT(*) n FROM findings GROUP BY 1",
                ),
            },
            "runs": {
                "by_kind": _tally(
                    store, "SELECT kind || ':' || status AS k, COUNT(*) n FROM runs GROUP BY 1"
                ),
                "findings_per_run": _tally(
                    store,
                    "SELECT kind AS k, ROUND(CAST(SUM(finding_count) AS REAL) / COUNT(*), 2) n"
                    " FROM runs WHERE status = 'ok' GROUP BY 1",
                ),
                "skips": _tally(
                    store,
                    "SELECT reason AS k, COUNT(*) n FROM runs WHERE status = 'skipped'"
                    " AND reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
                ),
                "failures": _tally(
                    store,
                    "SELECT SUBSTR(COALESCE(error,'no reason recorded'),1,80) AS k, COUNT(*) n"
                    " FROM runs WHERE status = 'failed' GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
                ),
            },
            "repositories": _rows(
                store,
                "SELECT repo_path, COUNT(*) open FROM findings"
                " WHERE status IN ('open','doing') AND (triage IS NULL OR triage != 'false')"
                " GROUP BY 1 ORDER BY 2 DESC LIMIT 40",
            ),
        }

    @server.tool(
        description=(
            "Work items from every repository, most severe first. Pass a repository "
            "path to narrow it, or words to search for. This is what to read when "
            "deciding whether the findings are worth acting on."
        )
    )
    def everywhere(
        query: str = "",
        repository: str = "",
        limit: int = 20,
        include_closed: bool = False,
        include_dismissed: bool = False,
    ) -> dict[str, Any]:
        where = repository or None
        statuses = () if include_closed else ACTIVE
        if query.strip():
            found = search_findings(store, query, where, statuses, limit)
        else:
            found = list_findings(store, where, statuses, limit, include_dismissed)
        return {"items": _with_counts(store, found)}

    @server.tool(
        description=(
            "The most recent runs across every repository, newest first, with what "
            "each one found and why it stopped."
        )
    )
    def runs(repository: str = "", limit: int = 20) -> dict[str, Any]:
        found = list_runs(store, repository or None, limit)
        return {
            "runs": [
                {
                    "id": one.id,
                    "repository": one.repo_path,
                    "kind": one.kind,
                    "status": one.status,
                    "reason": one.reason,
                    "started_at": one.started_at,
                    "duration_ms": one.duration_ms,
                    "findings": one.finding_count,
                    "tokens": one.prompt_tokens + one.completion_tokens,
                    "error": one.error,
                }
                for one in found
            ]
        }

    return server

"""The run log.

Every attempt is recorded, including the ones that were skipped and the ones that
failed. A repository that is never reviewed must be visible, with the reason.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from reviewrig.store.db import Store

Status = Literal["running", "ok", "failed", "skipped"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Run:
    id: str
    repo_path: str
    kind: str
    status: Status
    started_at: str
    reason: str | None = None
    base: str | None = None
    head: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    finding_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    backend: str | None = None
    error: str | None = None
    attempts: int = 1


def start(
    store: Store, repo_path: str | Path, kind: str, base: str | None, head: str | None
) -> Run:
    run = Run(
        id=new_id(),
        repo_path=str(repo_path),
        kind=kind,
        status="running",
        started_at=now(),
        base=base,
        head=head,
    )
    with store.write() as connection:
        connection.execute(
            """
            INSERT INTO runs (id, repo_path, kind, status, reason, base, head, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run.id, run.repo_path, run.kind, run.status, None, run.base, run.head, run.started_at),
        )
    return run


def finish(store: Store, run: Run) -> Run:
    run.finished_at = now()
    with store.write() as connection:
        connection.execute(
            """
            UPDATE runs SET status = ?, reason = ?, finished_at = ?, duration_ms = ?,
                finding_count = ?, prompt_tokens = ?, completion_tokens = ?,
                backend = ?, error = ?, attempts = ?
            WHERE id = ?
            """,
            (
                run.status,
                run.reason,
                run.finished_at,
                run.duration_ms,
                run.finding_count,
                run.prompt_tokens,
                run.completion_tokens,
                run.backend,
                run.error,
                run.attempts,
                run.id,
            ),
        )
    return run


def record_skip(store: Store, repo_path: str | Path, kind: str, reason: str, detail: str) -> Run:
    """A skip is a run too. Without a row, a repository that never runs is invisible.

    A repository that stays busy is skipped every cycle. Those repeats share one row and
    a count, so one blocked repository does not bury the rest of the log.
    """
    latest = list_runs(store, repo_path, limit=1)
    if latest and latest[0].status == "skipped" and latest[0].reason == reason:
        run = latest[0]
        run.attempts += 1
        run.error = detail or None
        return finish(store, run)
    run = start(store, repo_path, kind, None, None)
    run.status = "skipped"
    run.reason = reason
    run.error = detail or None
    run.duration_ms = 0
    return finish(store, run)


def _to_run(row: sqlite3.Row) -> Run:
    data = dict(row)
    return Run(
        id=str(data["id"]),
        repo_path=str(data["repo_path"]),
        kind=str(data["kind"]),
        status=cast(Status, str(data["status"])),
        started_at=str(data["started_at"]),
        reason=data["reason"],
        base=data["base"],
        head=data["head"],
        finished_at=data["finished_at"],
        duration_ms=data["duration_ms"],
        finding_count=int(data["finding_count"]),
        prompt_tokens=int(data["prompt_tokens"]),
        completion_tokens=int(data["completion_tokens"]),
        backend=data["backend"],
        error=data["error"],
        attempts=int(data["attempts"]),
    )


def list_runs(store: Store, repo_path: str | Path | None = None, limit: int = 100) -> list[Run]:
    where = "WHERE repo_path = ?" if repo_path is not None else ""
    parameters: list[object] = [str(repo_path)] if repo_path is not None else []
    parameters.append(limit)
    rows = store.query(
        f"SELECT * FROM runs {where} ORDER BY started_at DESC, rowid DESC LIMIT ?", parameters
    )
    return [_to_run(row) for row in rows]


def reviewed_head(store: Store, repo_path: str | Path) -> str | None:
    rows = store.query(
        "SELECT last_reviewed_head FROM repo_state WHERE repo_path = ?", (str(repo_path),)
    )
    return str(rows[0]["last_reviewed_head"]) if rows and rows[0]["last_reviewed_head"] else None


def set_reviewed_head(store: Store, repo_path: str | Path, head: str) -> None:
    with store.write() as connection:
        connection.execute(
            """
            INSERT INTO repo_state (repo_path, last_reviewed_head, last_reviewed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(repo_path) DO UPDATE SET
                last_reviewed_head = excluded.last_reviewed_head,
                last_reviewed_at = excluded.last_reviewed_at
            """,
            (str(repo_path), head, now()),
        )

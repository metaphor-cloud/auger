"""The repository table.

A repository that disappears from disk keeps its row and loses its `present` flag, so a
temporary unmount or a rename does not throw away its findings.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from auger.models import Remote, Repository
from auger.store.db import Store


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _row_to_repository(row: dict[str, object] | Sequence[object]) -> Repository:
    data = dict(row)  # type: ignore[arg-type]
    host = data["host"]
    remote = (
        Remote(host=str(host), namespace=str(data["namespace"]), name=str(data["remote_name"]))
        if host
        else None
    )
    return Repository(path=Path(str(data["path"])), remote=remote)


def record_scan(store: Store, found: Iterable[Repository], timestamp: str | None = None) -> int:
    """Store the result of one walk. Returns the number of repositories found."""
    stamp = timestamp or now()
    rows = [
        (
            str(repository.path),
            repository.name,
            repository.remote.host if repository.remote else None,
            repository.remote.namespace if repository.remote else None,
            repository.remote.name if repository.remote else None,
            stamp,
            stamp,
        )
        for repository in found
    ]
    with store.write() as connection:
        connection.executemany(
            """
            INSERT INTO repositories
                (path, name, host, namespace, remote_name, first_seen_at, last_seen_at, present)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(path) DO UPDATE SET
                name         = excluded.name,
                host         = excluded.host,
                namespace    = excluded.namespace,
                remote_name  = excluded.remote_name,
                last_seen_at = excluded.last_seen_at,
                present      = 1
            """,
            rows,
        )
        # Mark absence by path, not by timestamp. Two scans inside one second share a
        # timestamp, and a timestamp comparison would then leave stale rows present.
        paths = [row[0] for row in rows]
        if paths:
            connection.execute(
                "UPDATE repositories SET present = 0 WHERE path NOT IN "
                f"({','.join('?' * len(paths))})",
                paths,
            )
        else:
            connection.execute("UPDATE repositories SET present = 0")
    return len(rows)


def list_repositories(store: Store, present_only: bool = True) -> list[Repository]:
    sql = "SELECT * FROM repositories"
    if present_only:
        sql += " WHERE present = 1"
    sql += " ORDER BY path"
    return [_row_to_repository(row) for row in store.query(sql)]

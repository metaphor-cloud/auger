"""Look up findings for one repository."""

from __future__ import annotations

import sqlite3
from typing import Any


def by_severity(
    connection: sqlite3.Connection, repo: str, severity: str, limit: int = 50
) -> list[Any]:
    order = "last_seen_at DESC"
    return connection.execute(
        f"SELECT * FROM findings WHERE repo_path = '{repo}' AND severity = ?"
        f" ORDER BY {order} LIMIT ?",
        (severity, limit),
    ).fetchall()

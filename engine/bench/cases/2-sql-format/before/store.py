"""Look up findings for one repository."""

from __future__ import annotations

import sqlite3
from typing import Any


def by_severity(
    connection: sqlite3.Connection, repo: str, severity: str, limit: int = 50
) -> list[Any]:
    return connection.execute(
        "SELECT * FROM findings WHERE repo_path = ? AND severity = ? LIMIT ?",
        (repo, severity, limit),
    ).fetchall()

"""SQLite storage.

One file holds everything: repositories, runs, findings, and later the code index. The
rig is a single user desktop application, so one connection behind one lock is enough,
and it removes every question about concurrent writers.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DB_NAME = "reviewrig.db"

#: Applied in order. `PRAGMA user_version` records how many have run. Never edit a
#: migration that shipped: add a new one.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE repositories (
        path          TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        host          TEXT,
        namespace     TEXT,
        remote_name   TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at  TEXT NOT NULL,
        present       INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX repositories_org ON repositories (host, namespace);
    """,
)


class Store:
    """A connection behind a lock. Every method is synchronous and short."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._migrate()

    @classmethod
    def open(cls, home: Path) -> Store:
        return cls(home / DB_NAME)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        applied: int = self._connection.execute("PRAGMA user_version").fetchone()[0]
        for index, migration in enumerate(MIGRATIONS[applied:], start=applied + 1):
            self._connection.executescript(migration)
            # A pragma takes no parameter, and `index` is a loop counter, not user input.
            self._connection.execute(f"PRAGMA user_version = {index}")
            self._connection.commit()

    @property
    def version(self) -> int:
        with self._lock:
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Run statements in one transaction. A failure rolls the whole block back."""
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def query(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

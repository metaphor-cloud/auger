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
    """
    CREATE TABLE runs (
        id                TEXT PRIMARY KEY,
        repo_path         TEXT NOT NULL,
        kind              TEXT NOT NULL,
        status            TEXT NOT NULL,
        reason            TEXT,
        base              TEXT,
        head              TEXT,
        started_at        TEXT NOT NULL,
        finished_at       TEXT,
        duration_ms       INTEGER,
        finding_count     INTEGER NOT NULL DEFAULT 0,
        prompt_tokens     INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        backend           TEXT,
        error             TEXT
    );
    CREATE INDEX runs_repo ON runs (repo_path, started_at DESC);

    CREATE TABLE findings (
        fingerprint   TEXT PRIMARY KEY,
        repo_path     TEXT NOT NULL,
        source        TEXT NOT NULL,
        severity      TEXT NOT NULL,
        title         TEXT NOT NULL,
        detail        TEXT NOT NULL,
        suggestion    TEXT NOT NULL DEFAULT '',
        file          TEXT NOT NULL,
        line          INTEGER,
        confidence    REAL NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'open',
        triage        TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at  TEXT NOT NULL,
        times_seen    INTEGER NOT NULL DEFAULT 1,
        run_id        TEXT
    );
    CREATE INDEX findings_repo ON findings (repo_path, status, severity);

    CREATE TABLE repo_state (
        repo_path          TEXT PRIMARY KEY,
        last_reviewed_head TEXT,
        last_reviewed_at   TEXT,
        last_audit_at      TEXT
    );
    """,
    """
    ALTER TABLE runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1;
    """,
    """
    CREATE TABLE indexed_files (
        repo_path  TEXT NOT NULL,
        path       TEXT NOT NULL,
        blob_sha   TEXT NOT NULL,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        indexed_at TEXT NOT NULL,
        PRIMARY KEY (repo_path, path)
    );

    CREATE TABLE chunks (
        id         INTEGER PRIMARY KEY,
        repo_path  TEXT NOT NULL,
        path       TEXT NOT NULL,
        symbol     TEXT NOT NULL DEFAULT '',
        kind       TEXT NOT NULL DEFAULT '',
        start_line INTEGER NOT NULL,
        end_line   INTEGER NOT NULL,
        text       TEXT NOT NULL
    );
    CREATE INDEX chunks_file ON chunks (repo_path, path);
    CREATE INDEX chunks_symbol ON chunks (repo_path, symbol);

    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        text, symbol, content='chunks', content_rowid='id'
    );
    CREATE TRIGGER chunks_after_insert AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts (rowid, text, symbol) VALUES (new.id, new.text, new.symbol);
    END;
    CREATE TRIGGER chunks_after_delete AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts (chunks_fts, rowid, text, symbol)
        VALUES ('delete', old.id, old.text, old.symbol);
    END;

    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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
        self.vectors = _load_vector_extension(self._connection)
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

    def get_meta(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM meta WHERE key = ?", (key,))
        return str(rows[0]["value"]) if rows else None

    def set_meta(self, key: str, value: str) -> None:
        with self.write() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _load_vector_extension(connection: sqlite3.Connection) -> bool:
    """Load `sqlite-vec`. Returns False when it will not load.

    Retrieval by meaning needs it. Everything else works without it, so a machine where
    the extension refuses to load still reviews code, with keyword search only.
    """
    try:
        import sqlite_vec

        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
    except Exception:
        return False
    return True

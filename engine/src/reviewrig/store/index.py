"""The code index.

Three ways to find a chunk, because no single one is enough:

- by symbol, when the diff says which symbol changed;
- by keyword, which finds a caller by its name, in any language;
- by meaning, which finds the code that matters for a change that renamed nothing.

The vector table is created on first use, at the dimension the embedding model returned.
A model change moves the dimension, and the index is dropped and rebuilt.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from reviewrig.context.chunker import Chunk
from reviewrig.store.db import Store
from reviewrig.store.text import fts_query

VECTOR_TABLE = "chunk_vectors"
DIMENSION_KEY = "embedding_dimension"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    score: float = 0.0

    @property
    def label(self) -> str:
        where = f"{self.path}:{self.start_line}-{self.end_line}"
        return f"{self.symbol} ({where})" if self.symbol else where


def _to_hit(row: sqlite3.Row, score: float = 0.0) -> Hit:
    return Hit(
        chunk_id=int(row["id"]),
        path=str(row["path"]),
        symbol=str(row["symbol"]),
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        text=str(row["text"]),
        score=score,
    )


# --- files ------------------------------------------------------------------------


def stored_blobs(store: Store, repo_path: str | Path) -> dict[str, str]:
    rows = store.query(
        "SELECT path, blob_sha FROM indexed_files WHERE repo_path = ?", (str(repo_path),)
    )
    return {str(row["path"]): str(row["blob_sha"]) for row in rows}


def forget_files(store: Store, repo_path: str | Path, paths: Iterable[str]) -> int:
    targets = list(paths)
    if not targets:
        return 0
    with store.write() as connection:
        for path in targets:
            _drop_chunks(connection, str(repo_path), path)
            connection.execute(
                "DELETE FROM indexed_files WHERE repo_path = ? AND path = ?",
                (str(repo_path), path),
            )
    return len(targets)


def _drop_chunks(connection: sqlite3.Connection, repo_path: str, path: str) -> None:
    """Remove one file's chunks, and their vectors if the vector table exists yet.

    It does not exist until the first embedding, so an index built with no embedding
    model still adds and removes files.
    """
    ids = [
        int(row[0])
        for row in connection.execute(
            "SELECT id FROM chunks WHERE repo_path = ? AND path = ?", (repo_path, path)
        )
    ]
    if not ids:
        return
    if _has_vector_table(connection):
        marks = ",".join("?" * len(ids))
        connection.execute(f"DELETE FROM {VECTOR_TABLE} WHERE chunk_id IN ({marks})", ids)
    connection.execute("DELETE FROM chunks WHERE repo_path = ? AND path = ?", (repo_path, path))


def replace_file(
    store: Store, repo_path: str | Path, path: str, blob_sha: str, chunks: Sequence[Chunk]
) -> list[int]:
    """Swap one file's chunks for new ones. Returns the new chunk ids."""
    repo = str(repo_path)
    ids: list[int] = []
    with store.write() as connection:
        if _has_vector_table(connection):
            _drop_chunks(connection, repo, path)
        else:
            connection.execute("DELETE FROM chunks WHERE repo_path = ? AND path = ?", (repo, path))
        for chunk in chunks:
            cursor = connection.execute(
                """
                INSERT INTO chunks (repo_path, path, symbol, kind, start_line, end_line, text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo,
                    chunk.path,
                    chunk.symbol,
                    chunk.kind,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.text,
                ),
            )
            ids.append(int(cursor.lastrowid or 0))
        connection.execute(
            """
            INSERT INTO indexed_files (repo_path, path, blob_sha, chunk_count, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo_path, path) DO UPDATE SET
                blob_sha = excluded.blob_sha,
                chunk_count = excluded.chunk_count,
                indexed_at = excluded.indexed_at
            """,
            (repo, path, blob_sha, len(chunks), now()),
        )
    return ids


def chunk_count(store: Store, repo_path: str | Path | None = None) -> int:
    if repo_path is None:
        return int(store.query("SELECT COUNT(*) AS n FROM chunks")[0]["n"])
    return int(
        store.query("SELECT COUNT(*) AS n FROM chunks WHERE repo_path = ?", (str(repo_path),))[0][
            "n"
        ]
    )


# --- vectors ----------------------------------------------------------------------


def _has_vector_table(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE name = ?", (VECTOR_TABLE,)
    ).fetchall()
    return bool(rows)


def ensure_vectors(store: Store, dimension: int) -> bool:
    """Create the vector table, or rebuild it when the model's dimension changed."""
    if not store.vectors or dimension <= 0:
        return False
    current = store.get_meta(DIMENSION_KEY)
    with store.write() as connection:
        if current == str(dimension) and _has_vector_table(connection):
            return True
        connection.execute(f"DROP TABLE IF EXISTS {VECTOR_TABLE}")
        connection.execute(
            f"CREATE VIRTUAL TABLE {VECTOR_TABLE} USING vec0("
            f"chunk_id INTEGER PRIMARY KEY, embedding float[{dimension}])"
        )
    store.set_meta(DIMENSION_KEY, str(dimension))
    return True


def store_vectors(store: Store, ids: Sequence[int], vectors: Sequence[Sequence[float]]) -> int:
    if not ids or not store.vectors:
        return 0
    rows = [
        (int(chunk_id), json.dumps(list(vector)))
        for chunk_id, vector in zip(ids, vectors, strict=True)
    ]
    with store.write() as connection:
        connection.executemany(
            f"INSERT OR REPLACE INTO {VECTOR_TABLE} (chunk_id, embedding) VALUES (?, ?)", rows
        )
    return len(rows)


def search_vectors(
    store: Store, query: Sequence[float], repo_path: str | Path, limit: int = 20
) -> list[Hit]:
    """Nearest chunks by meaning. Returns nothing when the index is empty."""
    if not store.vectors or not query:
        return []
    try:
        rows = store.query(
            f"""
            SELECT c.*, v.distance AS distance
            FROM {VECTOR_TABLE} AS v
            JOIN chunks AS c ON c.id = v.chunk_id
            WHERE v.embedding MATCH ? AND k = ? AND c.repo_path = ?
            ORDER BY v.distance
            """,
            (json.dumps(list(query)), limit * 4, str(repo_path)),
        )
    except sqlite3.Error:
        return []
    return [_to_hit(row, 1.0 / (1.0 + float(row["distance"]))) for row in rows][:limit]


# --- keyword and symbol -----------------------------------------------------------


def search_text(store: Store, text: str, repo_path: str | Path, limit: int = 20) -> list[Hit]:
    query = fts_query(text)
    if not query:
        return []
    try:
        rows = store.query(
            """
            SELECT c.*, bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks AS c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ? AND c.repo_path = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, str(repo_path), limit),
        )
    except sqlite3.Error:
        return []
    return [_to_hit(row, 1.0 / (1.0 + abs(float(row["rank"])))) for row in rows]


def chunks_for_symbol(
    store: Store, repo_path: str | Path, symbol: str, limit: int = 5
) -> list[Hit]:
    rows = store.query(
        """
        SELECT * FROM chunks
        WHERE repo_path = ? AND (symbol = ? OR symbol LIKE ? OR symbol LIKE ?)
        ORDER BY length(symbol) LIMIT ?
        """,
        (str(repo_path), symbol, f"{symbol}.%", f"%.{symbol}", limit),
    )
    return [_to_hit(row, 1.0) for row in rows]


def chunks_in_file(store: Store, repo_path: str | Path, path: str) -> list[Hit]:
    rows = store.query(
        "SELECT * FROM chunks WHERE repo_path = ? AND path = ? ORDER BY start_line",
        (str(repo_path), path),
    )
    return [_to_hit(row) for row in rows]

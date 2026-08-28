"""What happened to a work item since it was recorded.

A note is append only. A journal that can be rewritten is not a record of anything, and
the whole reason an agent reads this is to learn what it already did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auger.store.db import Store

#: One note cannot fill a review prompt on its own.
MAX_NOTE_CHARS = 4000


@dataclass(frozen=True)
class Note:
    id: int
    fingerprint: str
    author: str
    written_at: str
    text: str


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def add_note(
    store: Store,
    fingerprint: str,
    text: str,
    author: str = "agent",
    timestamp: str | None = None,
) -> Note:
    """Append one note. Raises `ValueError` when the item does not exist."""
    body = text.strip()[:MAX_NOTE_CHARS]
    if not body:
        raise ValueError("a note needs text")
    stamp = timestamp or now()
    with store.write() as connection:
        found = connection.execute(
            "SELECT 1 FROM findings WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if found is None:
            raise ValueError(f"no item with id {fingerprint}")
        cursor = connection.execute(
            "INSERT INTO finding_notes (fingerprint, author, written_at, text) VALUES (?, ?, ?, ?)",
            (fingerprint, author, stamp, body),
        )
    return Note(
        id=int(cursor.lastrowid or 0),
        fingerprint=fingerprint,
        author=author,
        written_at=stamp,
        text=body,
    )


def notes_for(store: Store, fingerprint: str, limit: int = 50) -> list[Note]:
    """Every note on one item, oldest first, because it is a story."""
    rows = store.query(
        "SELECT * FROM finding_notes WHERE fingerprint = ? ORDER BY id LIMIT ?",
        (fingerprint, limit),
    )
    return [
        Note(
            id=int(row["id"]),
            fingerprint=str(row["fingerprint"]),
            author=str(row["author"]),
            written_at=str(row["written_at"]),
            text=str(row["text"]),
        )
        for row in rows
    ]


def note_counts(store: Store, fingerprints: list[str]) -> dict[str, int]:
    """How many notes each item holds. The list shows this without reading them."""
    if not fingerprints:
        return {}
    rows = store.query(
        f"SELECT fingerprint, COUNT(*) AS n FROM finding_notes "
        f"WHERE fingerprint IN ({','.join('?' * len(fingerprints))}) GROUP BY fingerprint",
        list(fingerprints),
    )
    return {str(row["fingerprint"]): int(row["n"]) for row in rows}

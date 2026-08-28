"""Findings, and the fingerprint that keeps them stable.

The same problem must keep one row across re-reviews. If a fingerprint moved whenever an
unrelated line changed, the list would fill with duplicates and a suppression would last
one run.

The fingerprint therefore ignores the line number and the exact words. It holds the
source, the file, a normalised title, and a normalised snippet of the code the finding
is about.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from reviewrig.store.db import Store
from reviewrig.store.text import fts_query

Severity = Literal["critical", "high", "medium", "low", "info"]
Status = Literal["open", "doing", "resolved", "suppressed"]
#: What kind of problem this is. Severity says how much it matters; this says what it
#: is, and it is what the map filters on.
Category = Literal["security", "correctness", "performance", "quality", "style", "task"]
CATEGORIES: tuple[str, ...] = (
    "security",
    "correctness",
    "performance",
    "quality",
    "style",
    "task",
)
#: What a person or an agent means by "not finished". `doing` is work in flight.
ACTIVE: tuple[str, ...] = ("open", "doing")

SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

_WORDS = re.compile(r"[^a-z0-9]+")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalise(text: str) -> str:
    """Lower case, punctuation removed, runs of space collapsed."""
    return _WORDS.sub(" ", text.lower()).strip()


def fingerprint(source: str, file: str, title: str, snippet: str = "") -> str:
    """A stable identity for one problem.

    The line number is left out on purpose. A finding that moves down the file because
    an import was added above it is the same finding.
    """
    material = "\x1f".join([source, file, normalise(title), normalise(snippet)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass
class Finding:
    repo_path: str
    source: str
    severity: Severity
    title: str
    detail: str
    file: str
    line: int | None = None
    suggestion: str = ""
    confidence: float = 0.0
    snippet: str = ""
    status: Status = "open"
    category: Category = "quality"
    opened_at: str | None = None
    triage: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""
    times_seen: int = 1
    run_id: str | None = None
    fingerprint: str = field(default="")

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = fingerprint(self.source, self.file, self.title, self.snippet)

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 5)


def record(store: Store, findings: Sequence[Finding], timestamp: str | None = None) -> int:
    """Store findings. A repeat updates its row and never adds a second one.

    A suppressed finding stays suppressed. That is the whole value of suppressing it.
    """
    stamp = timestamp or now()
    rows = [_row(finding, stamp) for finding in findings]
    if not rows:
        return 0
    with store.write() as connection:
        connection.executemany(UPSERT, rows)
    return len(rows)


UPSERT = """
    INSERT INTO findings (
        fingerprint, repo_path, source, severity, title, detail, suggestion,
        file, line, confidence, triage, first_seen_at, last_seen_at, run_id, category
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(fingerprint) DO UPDATE SET
        severity     = excluded.severity,
        category     = excluded.category,
        detail       = excluded.detail,
        suggestion   = excluded.suggestion,
        line         = excluded.line,
        confidence   = excluded.confidence,
        last_seen_at = excluded.last_seen_at,
        run_id       = excluded.run_id,
        times_seen   = findings.times_seen + 1
"""


def _row(finding: Finding, stamp: str) -> tuple[object, ...]:
    return (
        finding.fingerprint,
        finding.repo_path,
        finding.source,
        finding.severity,
        finding.title,
        finding.detail,
        finding.suggestion,
        finding.file,
        finding.line,
        finding.confidence,
        finding.triage,
        stamp,
        stamp,
        finding.run_id,
        finding.category,
    )


def record_one(
    store: Store, finding: Finding, timestamp: str | None = None
) -> tuple[Finding, bool]:
    """Store one item and say whether it was already there.

    An agent asks for this before it starts work. "You recorded this on Tuesday and
    it is still open" is the answer that stops it from doing the work twice.
    """
    stamp = timestamp or now()
    with store.write() as connection:
        before = connection.execute(
            "SELECT 1 FROM findings WHERE fingerprint = ?", (finding.fingerprint,)
        ).fetchone()
        connection.execute(UPSERT, _row(finding, stamp))
        row = connection.execute(
            "SELECT * FROM findings WHERE fingerprint = ?", (finding.fingerprint,)
        ).fetchone()
    return _to_finding(row), before is not None


def _to_finding(row: sqlite3.Row) -> Finding:
    data = dict(row)
    return Finding(
        fingerprint=str(data["fingerprint"]),
        repo_path=str(data["repo_path"]),
        source=str(data["source"]),
        severity=cast(Severity, str(data["severity"])),
        title=str(data["title"]),
        detail=str(data["detail"]),
        suggestion=str(data["suggestion"]),
        file=str(data["file"]),
        line=int(data["line"]) if data["line"] is not None else None,
        confidence=float(data["confidence"]),
        status=cast(Status, str(data["status"])),
        category=cast(Category, str(data["category"])),
        opened_at=data["opened_at"],
        triage=data["triage"],
        first_seen_at=str(data["first_seen_at"]),
        last_seen_at=str(data["last_seen_at"]),
        times_seen=int(data["times_seen"]),
        run_id=data["run_id"],
    )


def list_findings(
    store: Store,
    repo_path: str | Path | None = None,
    statuses: Iterable[str] = ACTIVE,
    limit: int = 500,
    include_dismissed: bool = False,
) -> list[Finding]:
    """Open findings, most severe first.

    A finding the model judged false stays in the database and out of the list. Deleting
    it would bring it back on the next scan; hiding it keeps the list worth reading.
    """
    clauses = []
    parameters: list[object] = []
    wanted = list(statuses)
    if wanted:
        clauses.append(f"status IN ({','.join('?' * len(wanted))})")
        parameters.extend(wanted)
    if repo_path is not None:
        clauses.append("repo_path = ?")
        parameters.append(str(repo_path))
    if not include_dismissed:
        clauses.append("(triage IS NULL OR triage != 'false')")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    parameters.append(limit)
    rows = store.query(
        f"""
        SELECT * FROM findings {where}
        ORDER BY CASE severity
            WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2
            WHEN 'low' THEN 3 ELSE 4 END, last_seen_at DESC
        LIMIT ?
        """,
        parameters,
    )
    return [_to_finding(row) for row in rows]


def search_findings(
    store: Store,
    text: str,
    repo_path: str | Path | None = None,
    statuses: Iterable[str] = (),
    limit: int = 20,
) -> list[Finding]:
    """Items that match free text, best match first.

    This is how an agent asks whether it has met a problem before. It searches the
    title and the detail, because it knows what it was working on, not the fingerprint.
    """
    query = fts_query(text)
    if not query:
        return []
    clauses = ["findings_fts MATCH ?"]
    parameters: list[object] = [query]
    if repo_path is not None:
        clauses.append("f.repo_path = ?")
        parameters.append(str(repo_path))
    wanted = list(statuses)
    if wanted:
        clauses.append(f"f.status IN ({','.join('?' * len(wanted))})")
        parameters.extend(wanted)
    parameters.append(limit)
    try:
        rows = store.query(
            f"""
            SELECT f.*, bm25(findings_fts) AS rank
            FROM findings_fts
            JOIN findings AS f ON f.rowid = findings_fts.rowid
            WHERE {" AND ".join(clauses)}
            ORDER BY rank
            LIMIT ?
            """,
            parameters,
        )
    except sqlite3.Error:
        return []
    return [_to_finding(row) for row in rows]


def get_finding(store: Store, fingerprint: str) -> Finding | None:
    rows = store.query("SELECT * FROM findings WHERE fingerprint = ?", (fingerprint,))
    return _to_finding(rows[0]) if rows else None


def set_status(store: Store, fingerprints: Sequence[str], status: Status) -> int:
    if not fingerprints:
        return 0
    with store.write() as connection:
        cursor = connection.execute(
            f"UPDATE findings SET status = ? WHERE fingerprint IN "
            f"({','.join('?' * len(fingerprints))})",
            [status, *fingerprints],
        )
    return cursor.rowcount


def mark_opened(store: Store, fingerprint: str, timestamp: str | None = None) -> None:
    """Remember that the user has read this one. The map stops drawing attention to it."""
    with store.write() as connection:
        connection.execute(
            "UPDATE findings SET opened_at = ? WHERE fingerprint = ? AND opened_at IS NULL",
            (timestamp or now(), fingerprint),
        )


def set_triage(store: Store, fingerprint: str, verdict: str, reason: str = "") -> None:
    """Record what the model decided about a static finding.

    The reason goes into the detail, so a dismissed finding says why it was dismissed.
    """
    with store.write() as connection:
        connection.execute(
            "UPDATE findings SET triage = ?, detail = "
            "CASE WHEN ? = '' THEN detail ELSE detail || char(10) || char(10) || "
            "'Triage: ' || ? END WHERE fingerprint = ?",
            (verdict, reason, reason, fingerprint),
        )


def counts(store: Store, repo_path: str | Path | None = None) -> dict[str, int]:
    """Open findings per severity, plus a total. The tray shows this."""
    where = "WHERE status IN ('open', 'doing') AND (triage IS NULL OR triage != 'false')"
    parameters: list[object] = []
    if repo_path is not None:
        where += " AND repo_path = ?"
        parameters.append(str(repo_path))
    rows = store.query(
        f"SELECT severity, COUNT(*) AS n FROM findings {where} GROUP BY severity", parameters
    )
    result = dict.fromkeys(SEVERITY_ORDER, 0)
    for row in rows:
        result[str(row["severity"])] = int(row["n"])
    result["total"] = sum(result.values())
    return result

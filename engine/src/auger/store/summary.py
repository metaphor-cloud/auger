"""One read for the landing page.

A dashboard that made a request per number would take a dozen round trips and show a
different moment in each one. This reads them together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from auger.store.db import Store
from auger.store.findings import SEVERITY_ORDER


@dataclass
class RepositorySummary:
    path: str
    name: str
    open_findings: int
    worst_severity: str
    last_run_at: str | None
    last_status: str | None


@dataclass
class Summary:
    repositories: int = 0
    excluded: int = 0
    findings: dict[str, int] = field(default_factory=dict)
    suppressed: int = 0
    dismissed: int = 0
    runs_today: int = 0
    runs_by_status: dict[str, int] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    last_run_at: str | None = None
    busiest: list[RepositorySummary] = field(default_factory=list)
    skipped_reasons: dict[str, int] = field(default_factory=dict)


def _counts(store: Store, sql: str, parameters: tuple[object, ...] = ()) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in store.query(sql, parameters)}


def summarise(store: Store, today: str, top: int = 5) -> Summary:
    """Everything the landing page shows, in one pass."""
    summary = Summary()

    open_by_severity = _counts(
        store,
        "SELECT severity, COUNT(*) FROM findings "
        "WHERE status IN ('open', 'doing') AND (triage IS NULL OR triage != 'false') "
        "GROUP BY severity",
    )
    summary.findings = {name: open_by_severity.get(name, 0) for name in SEVERITY_ORDER}
    summary.findings["total"] = sum(summary.findings.values())
    summary.suppressed = int(
        store.query("SELECT COUNT(*) FROM findings WHERE status = 'suppressed'")[0][0]
    )
    summary.dismissed = int(
        store.query("SELECT COUNT(*) FROM findings WHERE triage = 'false'")[0][0]
    )

    summary.runs_by_status = _counts(store, "SELECT status, COUNT(*) FROM runs GROUP BY status")
    summary.runs_today = int(
        store.query("SELECT COUNT(*) FROM runs WHERE started_at >= ?", (today,))[0][0]
    )
    totals = store.query(
        "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), "
        "MAX(started_at) FROM runs"
    )[0]
    summary.prompt_tokens = int(totals[0])
    summary.completion_tokens = int(totals[1])
    summary.last_run_at = str(totals[2]) if totals[2] else None

    summary.skipped_reasons = _counts(
        store,
        "SELECT reason, COUNT(*) FROM runs WHERE status = 'skipped' AND reason IS NOT NULL "
        "GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 6",
    )

    rows = store.query(
        """
        SELECT f.repo_path,
               COUNT(*) AS open_count,
               MIN(CASE f.severity
                   WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2
                   WHEN 'low' THEN 3 ELSE 4 END) AS worst
        FROM findings AS f
        WHERE f.status IN ('open', 'doing') AND (f.triage IS NULL OR f.triage != 'false')
        GROUP BY f.repo_path
        ORDER BY worst, open_count DESC
        LIMIT ?
        """,
        (top,),
    )
    names = list(SEVERITY_ORDER)
    for row in rows:
        path = str(row["repo_path"])
        latest = store.query(
            "SELECT started_at, status FROM runs WHERE repo_path = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (path,),
        )
        summary.busiest.append(
            RepositorySummary(
                path=path,
                name=path.rsplit("/", 1)[-1],
                open_findings=int(row["open_count"]),
                worst_severity=names[int(row["worst"])]
                if int(row["worst"]) < len(names)
                else "info",
                last_run_at=str(latest[0]["started_at"]) if latest else None,
                last_status=str(latest[0]["status"]) if latest else None,
            )
        )
    return summary

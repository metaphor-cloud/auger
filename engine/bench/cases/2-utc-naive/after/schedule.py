"""Whether an audit is due."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def is_due(last: str | None, every_hours: int) -> bool:
    if last is None or every_hours <= 0:
        return every_hours > 0
    when = datetime.fromisoformat(last)
    return datetime.now(UTC) - when >= timedelta(hours=every_hours)

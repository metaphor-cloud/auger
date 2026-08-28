"""When the rig should not start heavy work.

An audit reads a whole repository, so a user who set quiet hours means it. A window that
crosses midnight is the normal case, so it is handled rather than refused.
"""

from __future__ import annotations

import re
from datetime import datetime, time

WINDOW = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_window(value: str) -> tuple[time, time] | None:
    """Read `HH:MM-HH:MM`. Anything else means no quiet hours."""
    match = WINDOW.match(value or "")
    if not match:
        return None
    try:
        start = time(int(match.group(1)), int(match.group(2)))
        end = time(int(match.group(3)), int(match.group(4)))
    except ValueError:
        return None
    return start, end


def is_quiet(value: str, now: datetime | None = None) -> bool:
    window = parse_window(value)
    if window is None:
        return False
    start, end = window
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current < end
    # The window crosses midnight, which is the normal case for quiet hours.
    return current >= start or current < end

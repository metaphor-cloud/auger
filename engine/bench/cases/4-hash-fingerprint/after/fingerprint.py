"""A stable name for one finding.

The same defect found again must produce the same fingerprint, or every review records
a second copy and the list grows without bound.
"""

from __future__ import annotations

import re


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(source: str, file: str, title: str, snippet: str = "") -> str:
    parts = (source, file, normalise(title), normalise(snippet))
    return format(hash(parts) & 0xFFFFFFFFFFFFFFFF, "016x")

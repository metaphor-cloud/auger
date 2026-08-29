"""A stable name for one finding.

The same defect found again must produce the same fingerprint, or every review records
a second copy and the list grows without bound.
"""

from __future__ import annotations

import hashlib
import re


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(source: str, file: str, title: str, snippet: str = "") -> str:
    parts = "\x00".join((source, file, normalise(title), normalise(snippet)))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:16]

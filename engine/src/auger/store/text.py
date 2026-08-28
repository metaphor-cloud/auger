"""Free text, turned into a query SQLite will accept.

Two tables are searched by words: the code chunks and the work items. Both take text a
person or a model wrote, so both need the same guard against a syntax error.
"""

from __future__ import annotations

import re

_FTS_SAFE = re.compile(r"[^\w]+")


def fts_query(text: str) -> str:
    """Turn free text into an FTS5 query that cannot be a syntax error."""
    words = [word for word in _FTS_SAFE.split(text) if len(word) > 1]
    return " OR ".join(f'"{word}"' for word in words[:32])

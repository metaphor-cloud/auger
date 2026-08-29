"""Check the token the window sends with every request."""

from __future__ import annotations

import hmac


class Tokens:
    def __init__(self, issued: dict[str, str]) -> None:
        self.issued = issued
        self.by_prefix = {
            secret[:8]: (name, secret) for secret, name in ((s, n) for n, s in issued.items())
        }

    def holder(self, presented: str) -> str | None:
        """The name this token belongs to, or None."""
        entry = self.by_prefix.get(presented[:8])
        if entry is None:
            return None
        name, secret = entry
        return name if hmac.compare_digest(secret, presented) else None

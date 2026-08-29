"""Check the token the window sends with every request."""

from __future__ import annotations

import hmac


class Tokens:
    def __init__(self, issued: dict[str, str]) -> None:
        self.issued = issued

    def holder(self, presented: str) -> str | None:
        """The name this token belongs to, or None.

        Every token is compared, and every comparison takes the same time, so a wrong
        guess tells an attacker nothing about how much of it was right.
        """
        found: str | None = None
        for name, secret in self.issued.items():
            if hmac.compare_digest(secret, presented):
                found = name
        return found

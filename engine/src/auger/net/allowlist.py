"""Which destinations the rig may reach.

The rig exists to keep code on the machine, so the list is short and explicit: the local
model servers, and any forge the user turned on. Everything else is refused and logged.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
#: Every name that means this machine. A model server on the loopback address is the
#: normal case, and a user writes it in any of these forms.
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


@dataclass(frozen=True)
class Destination:
    host: str
    port: int

    @classmethod
    def parse(cls, value: str) -> Destination | None:
        """Read `https://host:port/path`, `host:port`, or `host`."""
        text = value.strip()
        if not text:
            return None
        # A bare `host` or `host:port` has no scheme. Treat it as https, which is what a
        # forge uses, and let an explicit `http://` ask for port 80.
        if "://" not in text:
            text = f"//{text}"
        try:
            parts = urlsplit(text, scheme="https")
            host = (parts.hostname or "").lower()
            port = parts.port or DEFAULT_PORTS.get(parts.scheme, 443)
        except ValueError:
            return None
        if not host:
            return None
        return cls(host=host, port=port)

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


class Allowlist:
    """An exact match on host and port. No wildcard, on purpose."""

    def __init__(self, destinations: Iterable[Destination] = ()) -> None:
        self._destinations: set[Destination] = set()
        for destination in destinations:
            self.add(destination)

    @classmethod
    def from_values(cls, values: Iterable[str]) -> Allowlist:
        parsed = (Destination.parse(value) for value in values)
        return cls([destination for destination in parsed if destination])

    def add(self, destination: Destination) -> None:
        self._destinations.add(destination)
        # A user writes one name for this machine and a client may use another.
        if destination.host in LOOPBACK:
            for name in LOOPBACK:
                self._destinations.add(Destination(name, destination.port))

    def allows(self, host: str, port: int) -> bool:
        return Destination(host.lower(), port) in self._destinations

    def allows_url(self, url: str) -> bool:
        destination = Destination.parse(url)
        return destination is not None and self.allows(destination.host, destination.port)

    def __len__(self) -> int:
        return len(self._destinations)

    def __iter__(self) -> Iterator[Destination]:
        return iter(sorted(self._destinations, key=str))

    def __str__(self) -> str:
        return ", ".join(str(destination) for destination in self)

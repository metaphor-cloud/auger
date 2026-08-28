"""Which tools a job may call.

The default is nothing. A tool that a repository did not ask for cannot be called on its
behalf, so a policy names each one, either exactly or with one wildcard per server.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

SEPARATOR = "."
WILDCARD = "*"


@dataclass(frozen=True)
class ToolName:
    server: str
    tool: str

    @property
    def qualified(self) -> str:
        return f"{self.server}{SEPARATOR}{self.tool}"

    @classmethod
    def parse(cls, value: str) -> ToolName | None:
        server, separator, tool = value.strip().partition(SEPARATOR)
        if not separator or not server or not tool:
            return None
        return cls(server=server, tool=tool)


class ToolAllowlist:
    """An exact name, or one server wildcard. No pattern beyond that."""

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self._exact: set[str] = set()
        self._servers: set[str] = set()
        for pattern in patterns:
            name = ToolName.parse(pattern)
            if name is None:
                continue
            if name.tool == WILDCARD:
                self._servers.add(name.server)
            else:
                self._exact.add(name.qualified)

    def allows(self, server: str, tool: str) -> bool:
        return server in self._servers or f"{server}{SEPARATOR}{tool}" in self._exact

    @property
    def empty(self) -> bool:
        return not self._exact and not self._servers

    def servers(self) -> set[str]:
        """Which servers this policy touches, so only those need to start."""
        return self._servers | {name.split(SEPARATOR, 1)[0] for name in self._exact}

    def __str__(self) -> str:
        return ", ".join(sorted(self._exact | {f"{name}.*" for name in self._servers}))

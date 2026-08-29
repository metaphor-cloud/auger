"""Drop the servers that stopped answering."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Server:
    name: str
    healthy: bool


class Registry:
    def __init__(self) -> None:
        self.servers: dict[str, Server] = {}

    def prune(self) -> list[str]:
        dead: list[str] = []
        for name, server in self.servers.items():
            if not server.healthy:
                dead.append(name)
                del self.servers[name]
        return dead

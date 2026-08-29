"""A queue of pending paths, per worker."""

from __future__ import annotations

from pathlib import Path


class Worker:
    def __init__(self, name: str, pending: list[Path] = []) -> None:
        self.name = name
        self.pending = pending

    def add(self, path: Path) -> None:
        self.pending.append(path)

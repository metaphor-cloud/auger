"""One semaphore per backend, so a server is kept full but never overrun."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class Backend:
    name: str
    max_concurrent: int = 4


class Gateway:
    def __init__(self, backends: dict[str, Backend]) -> None:
        self.backends = backends
        self._limits: dict[str, asyncio.Semaphore] = {}

    def _limit(self, name: str) -> asyncio.Semaphore:
        if name not in self._limits:
            self._limits[name] = asyncio.Semaphore(self.backends[name].max_concurrent)
        return self._limits[name]

    async def complete(self, name: str, prompt: str) -> str:
        async with self._limit(name):
            return await self._post(name, prompt)

    async def _post(self, name: str, prompt: str) -> str:
        return prompt

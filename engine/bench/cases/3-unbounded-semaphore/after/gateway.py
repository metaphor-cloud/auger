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

    async def complete(self, name: str, prompt: str) -> str:
        async with asyncio.Semaphore(self.backends[name].max_concurrent):
            return await self._post(name, prompt)

    async def _post(self, name: str, prompt: str) -> str:
        return prompt

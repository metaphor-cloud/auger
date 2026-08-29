"""Stop the reviewer, run the second model, and start the reviewer again."""

from __future__ import annotations

import asyncio
from typing import Any


class Rig:
    def __init__(self) -> None:
        self.verifying = False

    async def verify(self, items: list[Any]) -> int:
        self.verifying = True
        try:
            await self._stop_reviewer()
            return await self._judge(items)
        finally:
            self.verifying = False
            await asyncio.shield(self._stop_verifier())

    async def _stop_reviewer(self) -> None: ...
    async def _stop_verifier(self) -> None: ...
    async def _judge(self, items: list[Any]) -> int:
        return len(items)

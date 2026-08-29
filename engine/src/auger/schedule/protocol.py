"""What the scheduler needs from the rig.

The Rig owns the scheduler, so the scheduler must not import it. This protocol names the
few members it uses, which keeps the dependency one way and keeps the types honest.
"""

from __future__ import annotations

from typing import Any, Protocol

from auger.config import Config
from auger.forge import Registry
from auger.llm import Gateway, Health
from auger.mcp import McpRegistry
from auger.models import RepositoryView
from auger.sandbox import Selection
from auger.store import Store


class RigLike(Protocol):
    store: Store
    gateway: Gateway
    config: Config
    forges: Registry
    tools: McpRegistry
    selection: Selection

    def publish(self, event: str, **data: object) -> None: ...

    def repositories(self) -> list[RepositoryView]: ...

    verifying: bool

    async def verify_findings(self, limit: int = 200) -> Any: ...

    async def check_models(self) -> dict[str, Health]: ...

    async def ensure_models(self) -> dict[str, Health]: ...

    async def ensure_backend(self, name: str) -> Health: ...

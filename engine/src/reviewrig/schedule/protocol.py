"""What the scheduler needs from the rig.

The Rig owns the scheduler, so the scheduler must not import it. This protocol names the
few members it uses, which keeps the dependency one way and keeps the types honest.
"""

from __future__ import annotations

from typing import Protocol

from reviewrig.config import Config
from reviewrig.forge import Registry
from reviewrig.llm import Gateway
from reviewrig.mcp import McpRegistry
from reviewrig.models import RepositoryView
from reviewrig.sandbox import Selection
from reviewrig.store import Store


class RigLike(Protocol):
    store: Store
    gateway: Gateway
    config: Config
    forges: Registry
    tools: McpRegistry
    selection: Selection

    def publish(self, event: str, **data: object) -> None: ...

    def repositories(self) -> list[RepositoryView]: ...

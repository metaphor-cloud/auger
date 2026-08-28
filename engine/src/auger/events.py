"""In-process event bus.

The UI holds one SSE connection and receives every state change on it, so it never polls.

A subscriber registers when it is created, not at its first read. If registration waited
for the first read, an event published in between would be lost.

A slow client must not stall the engine, so each subscriber gets a bounded queue and
loses its oldest event when it falls behind.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self

QUEUE_SIZE = 256


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


class Subscription:
    def __init__(self, bus: EventBus, size: int = QUEUE_SIZE) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=size)
        self._closed = False
        bus._register(self)

    def _offer(self, event: Event) -> None:
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(event)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def get(self) -> Event:
        return await self._queue.get()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._unregister(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        return await self.get()


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[Subscription] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _register(self, subscription: Subscription) -> None:
        self._subscribers.add(subscription)

    def _unregister(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def publish(self, event: Event) -> None:
        for subscription in self._subscribers:
            subscription._offer(event)

    def subscribe(self) -> Subscription:
        return Subscription(self)

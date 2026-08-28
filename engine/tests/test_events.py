from __future__ import annotations

import asyncio

from reviewrig.events import QUEUE_SIZE, Event, EventBus


async def test_a_subscriber_receives_a_published_event() -> None:
    bus = EventBus()
    with bus.subscribe() as subscription:
        bus.publish(Event("run.started", {"repo": "a"}))
        assert await asyncio.wait_for(subscription.get(), timeout=1) == Event(
            "run.started", {"repo": "a"}
        )


async def test_an_event_published_before_the_first_read_is_not_lost() -> None:
    bus = EventBus()
    with bus.subscribe() as subscription:
        bus.publish(Event("early"))
        assert (await asyncio.wait_for(subscription.get(), timeout=1)).kind == "early"


async def test_a_slow_subscriber_loses_its_oldest_event_and_keeps_the_newest() -> None:
    bus = EventBus()
    with bus.subscribe() as subscription:
        for index in range(QUEUE_SIZE + 5):
            bus.publish(Event("tick", {"index": index}))
        assert subscription.pending == QUEUE_SIZE
        received = [await subscription.get() for _ in range(QUEUE_SIZE)]
    assert received[0].data["index"] == 5
    assert received[-1].data["index"] == QUEUE_SIZE + 4


async def test_every_subscriber_gets_the_event() -> None:
    bus = EventBus()
    with bus.subscribe() as first, bus.subscribe() as second:
        bus.publish(Event("tick"))
        assert (await first.get()).kind == "tick"
        assert (await second.get()).kind == "tick"


async def test_a_closed_subscriber_is_dropped() -> None:
    bus = EventBus()
    subscription = bus.subscribe()
    assert bus.subscriber_count == 1
    subscription.close()
    assert bus.subscriber_count == 0
    bus.publish(Event("tick"))
    assert subscription.pending == 0

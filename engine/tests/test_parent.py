from __future__ import annotations

from collections.abc import Callable

import pytest

from auger.parent import watch_for_reparent


def reader(values: list[int]) -> Callable[[], int]:
    remaining = list(values)
    return lambda: remaining.pop(0)


def test_it_fires_when_the_parent_process_id_changes() -> None:
    fired: list[bool] = []
    watch_for_reparent(
        read_ppid=reader([4321, 4321, 4321, 1]),
        on_reparent=lambda: fired.append(True),
        sleep=lambda _: None,
    )
    assert fired == [True]


def test_it_waits_while_the_parent_stays() -> None:
    """A stable parent means the watch never returns. Stop it from the sleep."""
    calls = 0

    def sleep(_: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise TimeoutError

    fired: list[bool] = []
    with pytest.raises(TimeoutError):
        watch_for_reparent(
            read_ppid=lambda: 4321,
            on_reparent=lambda: fired.append(True),
            sleep=sleep,
        )
    assert fired == []

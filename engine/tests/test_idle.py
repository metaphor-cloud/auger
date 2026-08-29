"""Working only while nobody is using the machine."""

from __future__ import annotations

from typing import Any

import pytest

from auger.watch import idle


def test_a_machine_left_alone_is_free() -> None:
    assert idle.Idle(seconds=600).free_for(300) is True
    assert idle.Idle(seconds=10).free_for(300) is False


def test_a_platform_that_cannot_say_never_blocks_the_work() -> None:
    """Refusing to review because the question could not be asked is worse than
    reviewing at a bad moment."""
    assert idle.Idle(seconds=0, known=False).free_for(3600) is True


def test_it_reads_the_time_the_operating_system_reports(monkeypatch: Any) -> None:
    class Result:
        stdout = '    | | |   "HIDIdleTime" = 547808042750\n'

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: Result())
    idle.forget()
    assert round(idle.measure().seconds) == 548


def test_an_answer_it_cannot_parse_is_not_an_answer(monkeypatch: Any) -> None:
    class Result:
        stdout = "no idle time here\n"

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: Result())
    idle.forget()
    assert idle.measure().known is False


def test_a_command_that_fails_is_not_an_answer(monkeypatch: Any) -> None:
    def raises(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no ioreg here")

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("subprocess.run", raises)
    idle.forget()
    assert idle.measure().known is False


def test_the_answer_is_held_for_a_few_seconds(monkeypatch: Any) -> None:
    """The gate is checked per task, and a review runs for minutes."""
    asked = []

    def once() -> idle.Idle:
        asked.append(1)
        return idle.Idle(seconds=99)

    monkeypatch.setattr(idle, "measure", once)
    idle.forget()
    assert idle.current(now=100.0).seconds == 99
    assert idle.current(now=101.0).seconds == 99
    assert len(asked) == 1
    assert idle.current(now=200.0).seconds == 99
    assert len(asked) == 2


@pytest.fixture(autouse=True)
def _clean() -> Any:
    idle.forget()
    yield
    idle.forget()

"""The queue decides order, and it never lets two reviews touch one repository."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from reviewrig.config import Policy
from reviewrig.config.schema import Backend, Config, ProfileEntry
from reviewrig.forge import Registry
from reviewrig.llm import Gateway
from reviewrig.mcp import McpRegistry
from reviewrig.models import Remote, Repository, RepositoryView
from reviewrig.net import Allowlist
from reviewrig.schedule import Scheduler, Task
from reviewrig.store import Store
from reviewrig.store.runs import list_runs
from tests.helpers import FakeModelServer, git_commit, git_init

Serve = Callable[[object], Awaitable[str]]

ANSWER = json.dumps({"findings": [{"file": "a.py", "title": "Leak", "severity": "high"}]})


class StubRig:
    """Only what the scheduler needs. The Rig owns the scheduler, not the other way."""

    def __init__(self, store: Store, gateway: Gateway, config: Config) -> None:
        self.store = store
        self.gateway = gateway
        self.config = config
        self.forges = Registry(config, gateway.client)
        self.tools = McpRegistry(config)
        self.events: list[tuple[str, dict[str, object]]] = []

    def publish(self, event: str, **data: object) -> None:
        self.events.append((event, data))

    def repositories(self) -> list[RepositoryView]:
        return []

    def kinds(self, name: str) -> list[dict[str, object]]:
        return [data for event, data in self.events if event == name]


def age(path: Path, seconds: float = 600) -> None:
    when = time.time() - seconds
    for entry in [path, path / ".git", *path.rglob("*")]:
        try:
            os.utime(entry, (when, when))
        except OSError:
            continue


def make_repository(root: Path, name: str) -> Repository:
    path = git_init(root / name, remote=f"git@github.com:acme/{name}.git")
    git_commit(path, {"a.py": "x = 1\n"}, "one")
    git_commit(path, {"a.py": "x = 2\n"}, "two")
    age(path)
    return Repository(path=path, remote=Remote("github.com", "acme", name))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def fake() -> FakeModelServer:
    fake = FakeModelServer()
    fake.reply = ANSWER
    return fake


@pytest.fixture
async def rig(store: Store, fake: FakeModelServer, serve: Serve) -> AsyncIterator[StubRig]:
    base = await serve(fake.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="m")})
    config.profile["balanced"].review = ProfileEntry(backend="review")
    config.schedule.retry_seconds = 5
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield StubRig(store, gateway, config)
    await gateway.aclose()


async def drain(scheduler: Scheduler, timeout: float = 15.0) -> None:
    await asyncio.wait_for(scheduler.queue.join(), timeout)


async def test_a_review_runs_and_the_events_say_so(rig: StubRig, tmp_path: Path) -> None:
    repository = make_repository(tmp_path, "alpha")
    scheduler = Scheduler(rig)
    await scheduler.start(workers=1)
    scheduler.submit(Task.review(repository, Policy()))
    await drain(scheduler)
    await scheduler.stop()

    assert [event for event, _ in rig.events][:2] == ["run.started", "run.finished"]
    assert rig.kinds("run.finished")[0]["status"] == "ok"
    assert rig.kinds("finding.new")[0]["severity"] == "high"


async def test_a_high_priority_repository_goes_first(rig: StubRig, tmp_path: Path) -> None:
    low = make_repository(tmp_path, "low")
    high = make_repository(tmp_path, "high")
    scheduler = Scheduler(rig)
    scheduler.submit(Task.review(low, Policy(priority=9)))
    scheduler.submit(Task.review(high, Policy(priority=1)))
    await scheduler.start(workers=1)
    await drain(scheduler)
    await scheduler.stop()

    order = [data["slug"] for data in rig.kinds("run.started")]
    assert order == ["github.com/acme/high", "github.com/acme/low"]


async def test_equal_priority_keeps_the_order_it_arrived(rig: StubRig, tmp_path: Path) -> None:
    first = make_repository(tmp_path, "first")
    second = make_repository(tmp_path, "second")
    scheduler = Scheduler(rig)
    scheduler.submit(Task.review(first, Policy()))
    scheduler.submit(Task.review(second, Policy()))
    await scheduler.start(workers=1)
    await drain(scheduler)
    await scheduler.stop()
    assert [data["slug"] for data in rig.kinds("run.started")] == [
        "github.com/acme/first",
        "github.com/acme/second",
    ]


async def test_the_same_work_is_not_queued_twice(rig: StubRig, tmp_path: Path) -> None:
    repository = make_repository(tmp_path, "alpha")
    scheduler = Scheduler(rig)
    assert scheduler.submit(Task.review(repository, Policy())) is True
    assert scheduler.submit(Task.review(repository, Policy())) is False
    assert scheduler.pending == 1


async def test_a_busy_repository_is_skipped_recorded_and_tried_again(
    rig: StubRig, tmp_path: Path
) -> None:
    """A repository that is never reviewed must be visible, with the reason."""
    repository = make_repository(tmp_path, "alpha")
    (repository.path / ".git" / "index.lock").write_text("", encoding="utf-8")
    rig.config.schedule.retry_seconds = 300  # Long enough that it will not run again here.
    scheduler = Scheduler(rig)
    await scheduler.start(workers=1)
    scheduler.submit(Task.review(repository, Policy()))
    await drain(scheduler)
    await asyncio.sleep(0.05)
    # Read this before stopping. `stop` cancels the deferred retry.
    assert scheduler.pending == 1  # It waits, it is not dropped.
    await scheduler.stop()

    skipped = rig.kinds("run.skipped")
    assert skipped[0]["reason"] == "git_operation"
    runs = list_runs(rig.store)
    assert runs[0].status == "skipped"
    assert runs[0].reason == "git_operation"


async def test_a_repository_that_becomes_free_is_reviewed(rig: StubRig, tmp_path: Path) -> None:
    repository = make_repository(tmp_path, "alpha")
    lock = repository.path / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    rig.config.schedule.retry_seconds = 1
    scheduler = Scheduler(rig)
    await scheduler.start(workers=1)
    # The idle timer is off here. Removing the lock touches the tree, and this test is
    # about the lock, not about the timer.
    scheduler.submit(Task.review(repository, Policy(idle_seconds=0)))
    await drain(scheduler)
    lock.unlink()
    for _ in range(60):
        if rig.kinds("run.finished"):
            break
        await asyncio.sleep(0.1)
    await scheduler.stop()
    assert rig.kinds("run.finished")


async def test_two_workers_never_review_one_repository_at_once(
    rig: StubRig, tmp_path: Path, fake: FakeModelServer
) -> None:
    repository = make_repository(tmp_path, "alpha")
    fake.delay_seconds = 0.2
    scheduler = Scheduler(rig)
    await scheduler.start(workers=4)
    for target in ("HEAD", "WORKTREE"):
        scheduler.submit(Task.review(repository, Policy(), target=target))
    await drain(scheduler)
    await asyncio.sleep(0.3)
    await scheduler.stop()
    assert fake.peak_concurrent <= 1


async def test_a_pause_stops_new_work_and_keeps_the_queue(rig: StubRig, tmp_path: Path) -> None:
    repository = make_repository(tmp_path, "alpha")
    scheduler = Scheduler(rig)
    await scheduler.start(workers=1)
    scheduler.pause()
    scheduler.submit(Task.review(repository, Policy()))
    await asyncio.sleep(0.2)
    assert rig.kinds("run.started") == []
    assert scheduler.pending == 1
    assert scheduler.paused is True

    scheduler.resume()
    await drain(scheduler)
    await scheduler.stop()
    assert rig.kinds("run.started")


async def test_a_crash_in_one_task_does_not_stop_the_worker(rig: StubRig, tmp_path: Path) -> None:
    good = make_repository(tmp_path, "good")
    missing = Repository(path=tmp_path / "gone", remote=Remote("github.com", "acme", "gone"))
    scheduler = Scheduler(rig)
    await scheduler.start(workers=1)
    scheduler.submit(Task.review(missing, Policy()))
    scheduler.submit(Task.review(good, Policy()))
    await drain(scheduler)
    await scheduler.stop()
    assert [data["slug"] for data in rig.kinds("run.finished")][-1] == "github.com/acme/good"

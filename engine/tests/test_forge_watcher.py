"""Which pull requests the rig picks up, and which it leaves alone."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import httpx
import pytest

from reviewrig.config import Config, Policy
from reviewrig.forge import Entry, ForgeState, Registry
from reviewrig.forge.github import GitHub
from reviewrig.llm import Gateway
from reviewrig.models import Remote, Repository, RepositoryView
from reviewrig.schedule import Scheduler, poll_pull_requests
from reviewrig.store import Store
from reviewrig.store.runs import Run, finish, start
from tests.helpers import FakeGitHub

Serve = Callable[[object], Awaitable[str]]
REPOSITORY = Repository(path=Path("/x/thing"), remote=Remote("github.com", "acme", "thing"))


class StubRig:
    def __init__(self, store: Store, config: Config, registry: Registry, policy: Policy) -> None:
        self.store = store
        self.config = config
        self.forges = registry
        self.gateway: Gateway = None  # type: ignore[assignment]
        self.policy = policy
        self.events: list[tuple[str, dict[str, object]]] = []

    def publish(self, event: str, **data: object) -> None:
        self.events.append((event, data))

    def repositories(self) -> list[RepositoryView]:
        return [RepositoryView(REPOSITORY, self.policy)]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def hub() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
async def registry(hub: FakeGitHub, serve: Serve) -> AsyncIterator[Registry]:
    base = await serve(hub.app())
    async with httpx.AsyncClient() as client:
        registry = Registry(Config(), client)
        registry.entries["github.com"] = Entry(
            name="github",
            forge=GitHub(client, base, "token", "github.com"),
            state=ForgeState(),
        )
        yield registry


def rig_for(store: Store, registry: Registry, policy: Policy) -> StubRig:
    return StubRig(store, Config(), registry, policy)


async def queued(store: Store, registry: Registry, policy: Policy) -> list[str]:
    rig = rig_for(store, registry, policy)
    scheduler = Scheduler(rig)
    await poll_pull_requests(rig, scheduler)
    return [scheduler.queue.get_nowait().target for _ in range(scheduler.queue.qsize())]


async def test_it_queues_a_pull_request_assigned_to_the_user(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    hub.add_pull(number=7, assignees=["ru"])
    assert await queued(store, registry, Policy()) == ["pull/7"]


async def test_it_leaves_someone_elses_pull_request_alone(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    hub.add_pull(number=7, assignees=["someone"])
    assert await queued(store, registry, Policy()) == []


async def test_it_can_be_told_to_read_every_pull_request(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    hub.add_pull(number=7, assignees=["someone"])
    policy = Policy(auto_review_assigned_prs=False)
    assert await queued(store, registry, policy) == ["pull/7"]


async def test_a_draft_pull_request_is_left_alone(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    """A draft is not ready for a reviewer."""
    hub.add_pull(number=7, assignees=["ru"], draft=True)
    assert await queued(store, registry, Policy()) == []


async def test_a_repository_with_mode_off_is_left_alone(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    hub.add_pull(number=7, assignees=["ru"])
    assert await queued(store, registry, Policy(mode="off")) == []


async def test_a_head_that_was_already_reviewed_is_not_queued_again(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    hub.add_pull(number=7, assignees=["ru"], sha="abc123")
    run: Run = start(store, REPOSITORY.path, "pr_review", "main", "abc123")
    run.status = "ok"
    finish(store, run)
    assert await queued(store, registry, Policy()) == []


async def test_a_new_commit_brings_the_pull_request_back(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    run: Run = start(store, REPOSITORY.path, "pr_review", "main", "old-sha")
    run.status = "ok"
    finish(store, run)
    hub.add_pull(number=7, assignees=["ru"], sha="new-sha")
    assert await queued(store, registry, Policy()) == ["pull/7"]


async def test_a_forge_that_refuses_does_not_stop_the_cycle(
    store: Store, registry: Registry, hub: FakeGitHub
) -> None:
    hub.rate_limited = True
    assert await queued(store, registry, Policy()) == []


async def test_the_egress_allowlist_follows_the_enabled_forges(tmp_path: Path) -> None:
    """A forge that is off must not be reachable."""
    from reviewrig.rig import Rig
    from reviewrig.settings import Settings

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('[defaults]\nmode = "draft"\n', encoding="utf-8")
    rig = Rig(Settings(host="127.0.0.1", port=0, token="t", log_level="debug", home=home))
    try:
        assert not rig.allowlist.allows("api.github.com", 443)
    finally:
        await rig.aclose()

    (home / "config.toml").write_text("[forge.github]\nenabled = true\n", encoding="utf-8")
    rig = Rig(Settings(host="127.0.0.1", port=0, token="t", log_level="debug", home=home))
    try:
        assert rig.allowlist.allows("api.github.com", 443)
    finally:
        await rig.aclose()

"""An audit reads the shape of a repository, not its code."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from auger.config import Config, Policy
from auger.config.schema import Backend, ProfileEntry
from auger.context import reindex
from auger.jobs.audit import audit, outline
from auger.llm import Gateway
from auger.models import Remote, Repository, RepositoryView
from auger.net import Allowlist
from auger.schedule import is_quiet
from auger.schedule.quiet import parse_window
from auger.schedule.watcher import audit_due
from auger.store import Store
from auger.store.findings import list_findings
from auger.store.runs import last_audit, set_audited
from tests.helpers import FakeModelServer, git_commit, git_init

Serve = Callable[[object], Awaitable[str]]

ANSWER = json.dumps(
    {
        "findings": [
            {
                "file": "writer.py",
                "severity": "medium",
                "title": "writer.py duplicates reader.py",
                "detail": "Both hold the same read path.",
                "confidence": 0.6,
            }
        ]
    }
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    path = git_init(tmp_path / "repo")
    git_commit(
        path,
        {
            "reader.py": (
                "def read(path):\n    return path\n\n\n"
                "class Reader:\n    def go(self):\n        return 1\n"
            ),
            "writer.py": "def write(path):\n    return path\n",
        },
        "start",
    )
    return Repository(path=path, remote=Remote("github.com", "acme", "thing"))


@pytest.fixture
def model() -> FakeModelServer:
    fake = FakeModelServer()
    fake.reply = ANSWER
    return fake


@pytest.fixture
async def gateway(model: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(model.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="m")})
    config.profile["balanced"].review = ProfileEntry(backend="review")
    config.profile["balanced"].triage = ProfileEntry(backend="review")
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


# --- the outline -------------------------------------------------------------------


async def test_the_outline_holds_the_symbols_and_their_sizes(
    store: Store, repository: Repository
) -> None:
    await reindex(store, None, repository.path)
    shape = outline(store, repository.path)
    assert "reader.py" in shape
    assert "read (" in shape
    assert "Reader (" in shape


async def test_the_outline_sends_no_code(store: Store, repository: Repository) -> None:
    """A repository of a thousand files fits as an outline and does not fit as source."""
    await reindex(store, None, repository.path)
    shape = outline(store, repository.path)
    assert "return path" not in shape


async def test_the_outline_stops_at_its_budget(store: Store, repository: Repository) -> None:
    await reindex(store, None, repository.path)
    shape = outline(store, repository.path, budget=20)
    assert len(shape) < 200
    assert "not listed" in shape


# --- the job -----------------------------------------------------------------------


async def test_an_audit_stores_what_it_found(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    outcome = await audit(store, gateway, repository, Policy())
    assert outcome.run.status == "ok"
    assert [finding.source for finding in list_findings(store)] == ["audit"]
    assert list_findings(store)[0].title.startswith("writer.py duplicates")


async def test_an_audit_records_when_it_ran(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    await audit(store, gateway, repository, Policy())
    assert last_audit(store, repository.path) is not None


async def test_the_model_is_asked_for_structure_and_not_for_line_defects(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    await audit(store, gateway, repository, Policy())
    system = model.requests[0]["messages"][0]["content"]
    assert "You cannot see any code" in system
    assert "or a defect inside a function" in system


async def test_the_audit_is_told_that_one_name_in_two_places_is_normal(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """An outline cannot tell a class from its extension, and it reported that as a
    duplicate. The prompt now says so, and duplicates are out of scope."""
    await audit(store, gateway, repository, Policy())
    system = model.requests[0]["messages"][0]["content"]
    assert "Two entries with one name is normal" in system
    assert "Do not report a duplicate" in system


async def test_the_answer_is_held_to_a_shape(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """Free decoding is what produced a confidence of `0. nine`."""
    await audit(store, gateway, repository, Policy())
    fmt = model.requests[0].get("response_format")
    assert fmt is not None
    assert fmt["type"] == "json_schema"
    assert (
        "severity" in fmt["json_schema"]["schema"]["properties"]["findings"]["items"]["properties"]
    )


async def test_the_repository_hints_reach_the_audit(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    await audit(store, gateway, repository, Policy(hints="This is a prototype."))
    assert "This is a prototype." in model.requests[0]["messages"][1]["content"]


async def test_a_repository_with_no_index_is_skipped(
    store: Store, gateway: Gateway, tmp_path: Path, model: FakeModelServer
) -> None:
    empty = Repository(path=git_init(tmp_path / "empty"), remote=None)
    outcome = await audit(store, gateway, empty, Policy())
    assert outcome.run.status == "skipped"
    assert outcome.run.reason == "empty_index"
    assert model.requests == []


async def test_a_model_that_is_down_fails_the_run_and_not_the_rig(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    gateway.config.backend["review"].url = "https://api.openai.com/v1"
    outcome = await audit(store, gateway, repository, Policy())
    assert outcome.run.status == "failed"
    assert outcome.run.reason == "model_failed"


# --- the schedule ------------------------------------------------------------------


class StubRig:
    def __init__(self, store: Store, policy: Policy, repository: Repository) -> None:
        self.store = store
        self.config = Config()
        self.policy = policy
        self.repository = repository

    def publish(self, event: str, **data: object) -> None:
        return None

    def repositories(self) -> list[RepositoryView]:
        return [RepositoryView(self.repository, self.policy)]


def test_an_audit_is_due_when_it_never_ran(store: Store, repository: Repository) -> None:
    rig = StubRig(store, Policy(), repository)
    assert len(audit_due(rig, None)) == 1  # type: ignore[arg-type]


def test_an_audit_is_not_due_again_straight_away(store: Store, repository: Repository) -> None:
    set_audited(store, repository.path)
    rig = StubRig(store, Policy(audit_hours=24), repository)
    assert audit_due(rig, None) == []  # type: ignore[arg-type]


def test_an_audit_is_due_again_after_its_interval(store: Store, repository: Repository) -> None:
    old = (datetime.now(UTC) - timedelta(hours=30)).isoformat(timespec="milliseconds")
    set_audited(store, repository.path, old)
    rig = StubRig(store, Policy(audit_hours=24), repository)
    assert len(audit_due(rig, None)) == 1  # type: ignore[arg-type]


def test_audits_can_be_turned_off(store: Store, repository: Repository) -> None:
    rig = StubRig(store, Policy(audit_hours=0), repository)
    assert audit_due(rig, None) == []  # type: ignore[arg-type]


def test_a_repository_in_off_mode_is_never_audited(store: Store, repository: Repository) -> None:
    rig = StubRig(store, Policy(mode="off"), repository)
    assert audit_due(rig, None) == []  # type: ignore[arg-type]


# --- quiet hours -------------------------------------------------------------------


def test_a_window_that_crosses_midnight_works() -> None:
    """That is the normal case for quiet hours."""
    assert is_quiet("22:00-07:00", datetime(2026, 1, 1, 23, 30)) is True
    assert is_quiet("22:00-07:00", datetime(2026, 1, 1, 3, 0)) is True
    assert is_quiet("22:00-07:00", datetime(2026, 1, 1, 12, 0)) is False


def test_a_window_inside_one_day_works() -> None:
    assert is_quiet("09:00-17:00", datetime(2026, 1, 1, 12, 0)) is True
    assert is_quiet("09:00-17:00", datetime(2026, 1, 1, 20, 0)) is False


@pytest.mark.parametrize("value", ["", "nonsense", "25:00-07:00", "22:00"])
def test_an_unusable_window_means_no_quiet_hours(value: str) -> None:
    assert parse_window(value) is None
    assert is_quiet(value, datetime(2026, 1, 1, 23, 30)) is False


async def test_a_claim_is_checked_against_the_outline_it_came_from(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """An outline cannot show a duplicate, so a claim of one is judged before it stands."""
    from auger.store.findings import list_findings as read

    model.reply = ANSWER
    await audit(store, gateway, repository, Policy())

    # The audit asks once, then the claim pass asks again with the evidence beneath it.
    assert len(model.requests) == 2
    judged = model.requests[1]["messages"][1]["content"]
    assert "claim: writer.py duplicates reader.py" in judged
    assert "evidence from the outline:" in judged
    assert read(store)[0].source == "audit"


def test_the_evidence_is_the_path_and_what_sits_beside_it() -> None:
    from auger.jobs.audit import evidence_for

    shape = "\n".join(
        [
            "api/client.py: Client (40), send (12)",
            "api/models.py: User (20)",
            "web/page.py: render (8)",
        ]
    )
    evidence = evidence_for(shape, "api/client.py")
    assert "api/client.py: Client (40)" in evidence
    assert "api/models.py" in evidence
    assert "web/page.py" not in evidence

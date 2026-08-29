"""A second model, arguing with the first."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from auger.config import Config, Policy
from auger.config.schema import Backend, ProfileEntry
from auger.jobs import diff_review
from auger.jobs.adversary import argue, messages_for
from auger.llm import Gateway
from auger.models import Remote, Repository
from auger.net import Allowlist
from auger.store import Store
from auger.store.findings import Finding, list_findings, record
from tests.helpers import FakeModelServer, git_commit, git_init

Serve = Callable[[object], Awaitable[str]]

FOUND = json.dumps(
    {
        "findings": [
            {
                "file": "a.py",
                "line": 2,
                "severity": "high",
                "category": "correctness",
                "title": "The handle is never closed",
                "detail": "open() is called and nothing closes it.",
                "confidence": 0.8,
            }
        ]
    }
)
REJECTED = json.dumps(
    {"verdicts": [{"id": 1, "verdict": "false", "reason": "the with block closes it"}]}
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    path = git_init(tmp_path / "repo")
    git_commit(path, {"a.py": "x = 1\n"}, "one")
    git_commit(path, {"a.py": "x = 1\ny = open('f')\n"}, "two")
    return Repository(path=path, remote=Remote("github.com", "acme", "thing"))


def finding(store: Store) -> Finding:
    one = Finding(
        repo_path="/repo",
        source="model",
        severity="high",
        title="The handle is never closed",
        detail="open() is called and nothing closes it.",
        file="a.py",
        line=2,
    )
    record(store, [one])
    return one


@pytest.fixture
def reviewer() -> FakeModelServer:
    fake = FakeModelServer()
    fake.reply = FOUND
    return fake


@pytest.fixture
def judge() -> FakeModelServer:
    fake = FakeModelServer()
    fake.reply = REJECTED
    return fake


@pytest.fixture
async def gateway(
    reviewer: FakeModelServer, judge: FakeModelServer, serve: Serve
) -> AsyncIterator[Gateway]:
    review_url = await serve(reviewer.app())
    verify_url = await serve(judge.app())
    config = Config(
        backend={
            "reviewer": Backend(url=f"{review_url}/v1", model="reviewer"),
            "adversary": Backend(url=f"{verify_url}/v1", model="adversary"),
        }
    )
    config.profile["balanced"].review = ProfileEntry(backend="reviewer")
    config.profile["balanced"].verify = ProfileEntry(backend="adversary")
    gateway = Gateway(config, Allowlist.from_values([review_url, verify_url]))
    yield gateway
    await gateway.aclose()


def test_the_judge_is_shown_the_change_and_the_claim() -> None:
    one = Finding(
        repo_path="/repo",
        source="model",
        severity="high",
        title="The handle is never closed",
        detail="nothing closes it",
        file="a.py",
        line=2,
    )
    messages = messages_for("--- a/a.py\n+++ b/a.py\n+y = open('f')", [one])
    body = messages[1].content
    assert "y = open('f')" in body
    assert "claim: The handle is never closed" in body
    assert "You are not being asked to review the change" in messages[0].content


async def test_a_rejected_finding_is_marked_and_not_deleted(
    store: Store, gateway: Gateway, judge: FakeModelServer
) -> None:
    """The disagreement is worth seeing, and the judge is not always right either."""
    one = finding(store)
    outcome = await argue(store, gateway, "a diff", [one], Policy())

    assert outcome.judged == 1
    assert outcome.rejected == 1
    assert judge.requests, "the second model was asked"
    kept = list_findings(store, include_dismissed=True)
    assert len(kept) == 1
    assert kept[0].triage == "false"
    assert "adversary" in (kept[0].detail or "")


async def test_a_judge_that_is_down_leaves_the_findings_alone(
    store: Store, gateway: Gateway
) -> None:
    """An unjudged finding still shows. Losing it would be worse."""
    one = finding(store)
    gateway.config.backend["adversary"].url = "http://127.0.0.1:1/v1"

    outcome = await argue(store, gateway, "a diff", [one], Policy())

    assert outcome.judged == 0
    assert outcome.problems
    assert list_findings(store)[0].triage is None


async def test_the_roles_trade_places_between_runs(
    store: Store,
    gateway: Gateway,
    repository: Repository,
    reviewer: FakeModelServer,
    judge: FakeModelServer,
) -> None:
    """Neither model's blind spots decide on their own."""
    policy = Policy(adversary=True, alternate=True)

    await diff_review.review(store, gateway, repository, policy)
    first = len(reviewer.requests)

    await diff_review.review(store, gateway, repository, policy, base="HEAD~1", target="HEAD")
    assert len(reviewer.requests) > first or len(judge.requests) > 1


async def test_the_adversary_is_off_unless_it_is_turned_on(
    store: Store, gateway: Gateway, repository: Repository, judge: FakeModelServer
) -> None:
    await diff_review.review(store, gateway, repository, Policy())
    assert judge.requests == []


def test_the_verdict_shape_is_held_to() -> None:
    from auger.jobs.triage import VERDICT_SCHEMA

    shape = json.dumps(VERDICT_SCHEMA)
    assert '"enum": ["true", "false", "uncertain"]' in shape

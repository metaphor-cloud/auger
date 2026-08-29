"""`complete` mode writes on a real pull request. It must never happen by accident."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import httpx
import pytest

from auger.config import Config, Policy, resolve_policy
from auger.config.schema import Backend, ProfileEntry
from auger.forge import (
    Comment,
    Entry,
    ForgeError,
    ForgeState,
    PostedReview,
    PullRequest,
    Repo,
)
from auger.forge.github import GitHub
from auger.jobs.pr_review import COMMENT_CONFIDENCE, review_pull, summary_text, to_comments
from auger.llm import Gateway
from auger.models import Remote, Repository
from auger.net import Allowlist
from auger.store import Store
from auger.store.findings import Finding, list_findings
from auger.store.runs import list_runs, pull_reviewed
from tests.helpers import FakeGitHub, FakeModelServer

Serve = Callable[[object], Awaitable[str]]
REPO = Repo(owner="acme", name="thing")
REPOSITORY = Repository(path=Path("/x/thing"), remote=Remote("github.com", "acme", "thing"))

ANSWER = json.dumps(
    {
        "findings": [
            {
                "file": "reader.py",
                "line": 2,
                "severity": "high",
                "title": "File handle is never closed",
                "detail": "open() leaks the descriptor.",
                "suggestion": "Use a context manager.",
                "confidence": 0.9,
            },
            {
                "file": "reader.py",
                "line": 3,
                "severity": "low",
                "title": "Maybe rename this",
                "detail": "Not sure.",
                "confidence": 0.2,
            },
        ]
    }
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def hub() -> FakeGitHub:
    hub = FakeGitHub()
    hub.add_pull(number=7, assignees=["ru"])
    return hub


@pytest.fixture
def model() -> FakeModelServer:
    fake = FakeModelServer()
    fake.reply = ANSWER
    return fake


@pytest.fixture
async def entry(hub: FakeGitHub, serve: Serve) -> AsyncIterator[Entry]:
    base = await serve(hub.app())
    async with httpx.AsyncClient() as client:
        yield Entry(
            name="github",
            forge=GitHub(client, base, "token", "github.com"),
            state=ForgeState(user="ru"),
        )


@pytest.fixture
async def gateway(model: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(model.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="m")})
    config.profile["balanced"].review = ProfileEntry(backend="review")
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


async def one_pull(entry: Entry) -> PullRequest:
    return (await entry.forge.pull_requests(REPO))[0]


# --- the modes ---------------------------------------------------------------------


async def test_draft_mode_writes_a_review_that_waits(
    store: Store, gateway: Gateway, entry: Entry, hub: FakeGitHub
) -> None:
    pull = await one_pull(entry)
    outcome = await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="draft"))
    assert outcome.run.status == "ok"
    assert "event" not in hub.reviews[0]
    assert outcome.posted is not None
    assert outcome.posted.submitted is False


async def test_complete_mode_submits(
    store: Store, gateway: Gateway, entry: Entry, hub: FakeGitHub
) -> None:
    pull = await one_pull(entry)
    await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="complete"))
    assert hub.reviews[0]["event"] == "COMMENT"


async def test_off_mode_writes_nothing_and_costs_nothing(
    store: Store, gateway: Gateway, entry: Entry, hub: FakeGitHub, model: FakeModelServer
) -> None:
    pull = await one_pull(entry)
    outcome = await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="off"))
    assert outcome.run.status == "skipped"
    assert outcome.run.reason == "mode_off"
    assert hub.reviews == []
    assert model.requests == []


async def test_the_findings_are_stored_like_any_other(
    store: Store, gateway: Gateway, entry: Entry
) -> None:
    pull = await one_pull(entry)
    await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="draft"))
    assert len(list_findings(store)) == 2


async def test_a_reviewed_head_is_not_reviewed_again(
    store: Store, gateway: Gateway, entry: Entry
) -> None:
    """A pull request that gains no commit must cost nothing on the next poll."""
    pull = await one_pull(entry)
    await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="draft"))
    assert pull_reviewed(store, REPOSITORY.path, "abc123") is True
    assert pull_reviewed(store, REPOSITORY.path, "other-sha") is False


async def test_a_forge_that_refuses_fails_the_run_and_not_the_rig(
    store: Store, gateway: Gateway, entry: Entry, hub: FakeGitHub
) -> None:
    pull = await one_pull(entry)
    hub.rate_limited = True
    outcome = await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="draft"))
    assert outcome.run.status == "failed"
    assert outcome.run.reason == "forge_failed"
    assert list_runs(store)[0].status == "failed"


async def test_a_review_that_cannot_be_posted_still_keeps_its_findings(
    store: Store, gateway: Gateway, entry: Entry, hub: FakeGitHub
) -> None:
    pull = await one_pull(entry)

    async def refuse(
        repo: Repo, pull: PullRequest, summary: str, comments: list[Comment], submit: bool
    ) -> PostedReview:
        raise ForgeError("posting refused")

    entry.forge.post_review = refuse  # type: ignore[method-assign]
    outcome = await review_pull(store, gateway, entry, REPO, pull, REPOSITORY, Policy(mode="draft"))
    assert outcome.run.status == "ok"
    assert outcome.run.error == "posting refused"
    assert len(list_findings(store)) == 2


# --- what reaches the pull request ---------------------------------------------------


def finding(confidence: float, line: int | None = 2, severity: str = "high") -> Finding:
    return Finding(
        repo_path="/x/thing",
        source="model",
        severity=severity,  # type: ignore[arg-type]
        title="Leak",
        detail="it leaks",
        file="reader.py",
        line=line,
        confidence=confidence,
    )


def test_only_a_confident_finding_becomes_a_line_comment() -> None:
    """A wrong comment on someone else's pull request costs more than a missed one."""
    comments = to_comments([finding(0.9), finding(0.2)])
    assert len(comments) == 1
    assert comments[0].line == 2


def test_a_finding_with_no_line_never_becomes_a_line_comment() -> None:
    assert to_comments([finding(0.9, line=None)]) == []


def test_the_confidence_bar_is_named_and_not_a_magic_number() -> None:
    assert 0.0 < COMMENT_CONFIDENCE < 1.0


def test_a_low_confidence_finding_still_reaches_the_summary() -> None:
    text = summary_text([finding(0.2)], "draft")
    assert "low confidence" in text
    assert "reader.py:2" in text


def test_the_summary_says_a_draft_is_a_draft() -> None:
    assert "draft" in summary_text([finding(0.9)], "draft").lower()
    assert "draft" not in summary_text([finding(0.9)], "complete").lower()


def test_a_clean_change_says_so() -> None:
    assert "nothing to report" in summary_text([], "draft")


# --- the policy levels ---------------------------------------------------------------


def test_a_repository_can_turn_off_a_forge_wide_complete_mode() -> None:
    """The narrow level wins. This is what stops a surprise comment on a real team."""
    config = Config.model_validate(
        {
            "defaults": {"mode": "draft"},
            "org": {"github.com/acme": {"mode": "complete"}},
            "repo": {"/x/thing": {"mode": "off"}},
        }
    )
    assert resolve_policy(REPOSITORY, config).mode == "off"


def test_auto_review_can_be_turned_off_for_one_organisation() -> None:
    config = Config.model_validate(
        {"org": {"github.com/acme": {"auto_review_assigned_prs": False}}}
    )
    assert resolve_policy(REPOSITORY, config).auto_review_assigned_prs is False

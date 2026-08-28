"""The whole path, end to end: a real repository, a real diff, a real HTTP model."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from reviewrig.config import Policy
from reviewrig.config.schema import Backend, Config, ProfileEntry
from reviewrig.jobs import diff_review
from reviewrig.llm import Gateway
from reviewrig.models import Remote, Repository
from reviewrig.net import Allowlist
from reviewrig.store import Store
from reviewrig.store.findings import list_findings
from reviewrig.store.runs import list_runs, reviewed_head
from reviewrig.watch import git
from tests.helpers import FakeModelServer, git_commit, git_init

Serve = Callable[[object], Awaitable[str]]

BUG = """\
def read(path):
    handle = open(path)
    return handle.read()
"""

ANSWER = json.dumps(
    {
        "findings": [
            {
                "file": "reader.py",
                "line": 2,
                "severity": "high",
                "title": "File handle is never closed",
                "detail": "open() without a context manager leaks the descriptor.",
                "suggestion": "Use `with open(path) as handle:`.",
                "confidence": 0.9,
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
    git_commit(path, {"reader.py": "def read(path):\n    return path\n"}, "start")
    return Repository(path=path, remote=Remote("github.com", "acme", "thing"))


@pytest.fixture
def fake() -> FakeModelServer:
    fake = FakeModelServer()
    fake.reply = ANSWER
    return fake


@pytest.fixture
async def gateway(fake: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(fake.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="review-model")})
    config.profile["balanced"].review = ProfileEntry(backend="review", max_tokens=1000)
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


POLICY = Policy()


async def test_a_review_stores_the_finding(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    outcome = await diff_review.review(store, gateway, repository, POLICY)

    assert outcome.run.status == "ok"
    assert outcome.run.finding_count == 1
    findings = list_findings(store)
    assert len(findings) == 1
    assert findings[0].title == "File handle is never closed"
    assert findings[0].severity == "high"
    assert findings[0].file == "reader.py"
    assert findings[0].source == "model"


async def test_the_run_records_what_it_cost(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    outcome = await diff_review.review(store, gateway, repository, POLICY)
    assert outcome.run.prompt_tokens == 11
    assert outcome.run.completion_tokens == 7
    assert outcome.run.backend == "review"
    assert outcome.run.duration_ms is not None


async def test_the_model_sees_the_diff_and_the_hints(
    store: Store, gateway: Gateway, repository: Repository, fake: FakeModelServer
) -> None:
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    policy = Policy(hints="Treat a leaked descriptor as critical.")
    await diff_review.review(store, gateway, repository, policy)

    prompt = fake.requests[0]["messages"][1]["content"]
    assert "+    handle = open(path)" in prompt
    assert "Treat a leaked descriptor as critical." in prompt
    assert "github.com/acme/thing" in prompt


async def test_a_second_review_of_the_same_change_adds_no_second_finding(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    await diff_review.review(store, gateway, repository, POLICY)
    await diff_review.review(store, gateway, repository, POLICY)
    assert len(list_findings(store)) == 1
    assert list_findings(store)[0].times_seen == 2


async def test_it_remembers_the_commit_it_reviewed(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    """The watcher compares this with HEAD, so a repository is reviewed once per change."""
    sha = git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    await diff_review.review(store, gateway, repository, POLICY)
    assert reviewed_head(store, repository.path) == sha


async def test_a_range_review_covers_every_commit_in_it(
    store: Store, gateway: Gateway, repository: Repository, fake: FakeModelServer
) -> None:
    """Ten commits overnight are reviewed once, not ten times."""
    base = git.head(repository.path)
    git_commit(repository.path, {"reader.py": BUG}, "two")
    git_commit(repository.path, {"writer.py": "def write():\n    pass\n"}, "three")
    await diff_review.review(store, gateway, repository, POLICY, base=base)
    prompt = fake.requests[0]["messages"][1]["content"]
    assert "reader.py" in prompt
    assert "writer.py" in prompt


async def test_an_empty_diff_is_skipped_and_costs_nothing(
    store: Store, gateway: Gateway, repository: Repository, fake: FakeModelServer
) -> None:
    outcome = await diff_review.review(store, gateway, repository, POLICY, target="WORKTREE")
    assert outcome.run.status == "skipped"
    assert outcome.run.reason == "empty_diff"
    assert fake.requests == []


async def test_uncommitted_work_can_be_reviewed(
    store: Store, gateway: Gateway, repository: Repository, fake: FakeModelServer
) -> None:
    (repository.path / "reader.py").write_text(BUG, encoding="utf-8")
    outcome = await diff_review.review(store, gateway, repository, POLICY, target="WORKTREE")
    assert outcome.run.status == "ok"
    assert "+    handle = open(path)" in fake.requests[0]["messages"][1]["content"]


async def test_an_unreadable_answer_fails_the_run_and_not_the_rig(
    store: Store, gateway: Gateway, repository: Repository, fake: FakeModelServer
) -> None:
    fake.reply = "I could not review this."
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    outcome = await diff_review.review(store, gateway, repository, POLICY)
    assert outcome.run.status == "ok"
    assert outcome.run.finding_count == 0
    assert outcome.problems


async def test_one_malformed_finding_does_not_lose_the_others(
    store: Store, gateway: Gateway, repository: Repository, fake: FakeModelServer
) -> None:
    fake.reply = json.dumps(
        {
            "findings": [
                {"nothing": "useful"},
                {"file": "reader.py", "title": "Leak", "severity": "high"},
            ]
        }
    )
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    outcome = await diff_review.review(store, gateway, repository, POLICY)
    assert outcome.run.finding_count == 1
    assert outcome.problems


async def test_a_backend_that_is_not_on_the_allowlist_fails_the_run_and_not_the_rig(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    """A typo in a backend URL must fail one run, not crash a worker."""
    gateway.config.backend["review"].url = "https://api.openai.com/v1"
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    outcome = await diff_review.review(store, gateway, repository, POLICY)
    assert outcome.run.status == "failed"
    assert outcome.run.reason == "model_failed"
    assert list_runs(store)[0].status == "failed"


async def test_every_attempt_appears_in_the_run_log(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    git_commit(repository.path, {"reader.py": BUG}, "add a reader")
    await diff_review.review(store, gateway, repository, POLICY)
    await diff_review.review(store, gateway, repository, POLICY, target="WORKTREE")
    statuses = [run.status for run in list_runs(store)]
    assert sorted(statuses) == ["ok", "skipped"]

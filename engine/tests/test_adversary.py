"""A second model, arguing with the first."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from auger.config import Config, Policy
from auger.config.schema import Backend, ProfileEntry
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


def test_the_judge_is_shown_the_code_and_the_claim(tmp_path: Path) -> None:
    """The change is history by the time the sweep runs, so it judges the code."""
    (tmp_path / "a.py").write_text("x = 1\ny = open('f')\nz = 3\n")
    one = Finding(
        repo_path=str(tmp_path),
        source="model",
        severity="high",
        title="The handle is never closed",
        detail="nothing closes it",
        file="a.py",
        line=2,
    )
    body = messages_for([one])[1].content
    assert "2: y = open('f')" in body
    assert "claim: The handle is never closed" in body
    assert "reported by: model" in body


def test_a_file_that_is_gone_supports_nothing(tmp_path: Path) -> None:
    one = Finding(
        repo_path=str(tmp_path),
        source="audit",
        severity="medium",
        title="a claim about a file that was deleted",
        detail="it says something",
        file="gone.py",
    )
    assert "could not be read" in messages_for([one])[1].content


async def test_a_rejected_finding_is_marked_and_not_deleted(
    store: Store, gateway: Gateway, judge: FakeModelServer
) -> None:
    """The disagreement is worth seeing, and the judge is not always right either."""
    one = finding(store)
    outcome = await argue(store, gateway, [one], Policy())

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

    outcome = await argue(store, gateway, [one], Policy())

    assert outcome.judged == 0
    assert outcome.problems
    assert list_findings(store)[0].triage is None


def test_the_verdict_shape_is_held_to() -> None:
    from auger.jobs.verdicts import VERDICT_SCHEMA

    shape = json.dumps(VERDICT_SCHEMA)
    assert '"enum": ["true", "false", "uncertain"]' in shape


async def test_the_sweep_swaps_the_models_and_gives_the_memory_back(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Two capable models do not fit at once, so the reviewer stops before the other
    starts, and the other stops when it is done."""
    from auger.llm import Health
    from auger.rig import Rig
    from auger.settings import Settings
    from auger.store.findings import record

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        "[defaults]\nadversary = true\n\n"
        '[backend.local-review]\nurl = "http://127.0.0.1:1/v1"\n\n'
        '[backend.local-adversary]\nurl = "http://127.0.0.1:2/v1"\n\n'
        '[profile.balanced.review]\nbackend = "local-review"\n\n'
        '[profile.balanced.verify]\nbackend = "local-adversary"\n',
        encoding="utf-8",
    )
    rig = Rig(Settings(host="127.0.0.1", port=0, token="t", log_level="debug", home=home))
    try:
        record(
            rig.store,
            [
                Finding(
                    repo_path=str(tmp_path),
                    source="audit",
                    severity="high",
                    title="a claim nothing has judged",
                    detail="it says something",
                    file="a.py",
                )
            ],
        )

        order: list[str] = []
        monkeypatch.setattr(rig, "stop_models", lambda name=None: order.append(f"stop {name}"))

        async def started(client: Any, backends: dict[str, Any]) -> dict[str, Health]:
            order.append(f"start {next(iter(backends))}")
            return {name: Health(name=name, url="", up=True) for name in backends}

        monkeypatch.setattr(rig.supervisor, "ensure", started)

        async def judged(*args: Any, **kwargs: Any) -> Any:
            order.append("judge")
            assert rig.verifying is True
            from auger.jobs.adversary import Argument

            return Argument(judged=1, rejected=1)

        monkeypatch.setattr("auger.rig.argue", judged)

        outcome = await rig.verify_findings()
    finally:
        await rig.aclose()

    assert order == ["stop local-review", "start local-adversary", "judge", "stop local-adversary"]
    assert outcome.judged == 1
    assert rig.verifying is False


async def test_the_sweep_does_nothing_when_there_is_nothing_to_judge(
    tmp_path: Path,
) -> None:
    from auger.rig import Rig
    from auger.settings import Settings

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[defaults]\nadversary = true\n", encoding="utf-8")
    rig = Rig(Settings(host="127.0.0.1", port=0, token="t", log_level="debug", home=home))
    try:
        assert (await rig.verify_findings()).judged == 0
    finally:
        await rig.aclose()


def test_only_findings_nobody_has_judged_are_swept(tmp_path: Path) -> None:
    from auger.store.db import Store
    from auger.store.findings import record, set_status, set_triage, unjudged

    store = Store.open(tmp_path)
    try:
        made = [
            Finding(
                repo_path="/r",
                source="audit",
                severity=sev,
                title=f"claim {sev}",
                detail="d",
                file="a.py",
            )
            for sev in ("high", "medium", "low")
        ]
        record(store, made)
        set_triage(store, made[1].fingerprint, "true", "checked")
        set_status(store, [made[2].fingerprint], "resolved")

        waiting = unjudged(store)
        assert [one.title for one in waiting] == ["claim high"]
    finally:
        store.close()

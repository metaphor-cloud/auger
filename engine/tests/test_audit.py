"""An audit uses the outline to choose files, then reads the code in them."""

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

CHOICE = json.dumps({"files": [{"path": "writer.py", "why": "it writes a path"}]})

ANSWER = json.dumps(
    {
        "findings": [
            {
                "file": "writer.py",
                "line": 2,
                "severity": "medium",
                "title": "write returns its argument and writes nothing",
                "detail": "The body returns `path` without opening it.",
                "confidence": 0.6,
            }
        ]
    }
)


def answers(fake: FakeModelServer, *replies: str) -> None:
    """What the fake says, in order: the choice first, then the review."""
    fake.replies = list(replies) or [CHOICE, ANSWER]


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
    answers(fake)
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
    assert list_findings(store)[0].title.startswith("write returns its argument")


async def test_an_audit_records_when_it_ran(
    store: Store, gateway: Gateway, repository: Repository
) -> None:
    await audit(store, gateway, repository, Policy())
    assert last_audit(store, repository.path) is not None


async def test_the_choosing_pass_is_not_asked_for_defects(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """It has seen no code. Everything it said about one was a guess, and the guesses
    were reported as findings for months."""
    await audit(store, gateway, repository, Policy())
    system = model.requests[0]["messages"][0]["content"]
    assert "choosing which files to read" in system
    assert "you have not seen any code, so do not describe one" in system


async def test_the_reviewing_pass_must_point_at_a_line(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """A finding with no line is one nobody can check against the code."""
    await audit(store, gateway, repository, Policy())
    system = model.requests[1]["messages"][0]["content"]
    assert "point at the line that has it" in system
    assert "Do not report style" in system


async def test_the_answer_is_held_to_a_shape(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """Free decoding is what produced a confidence of `0. nine`."""
    await audit(store, gateway, repository, Policy())
    fmt = model.requests[1].get("response_format")
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


async def test_the_first_pass_chooses_and_the_second_pass_reads_the_code(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """The outline says which files are worth reading. It never says what is wrong with
    them: a claim drawn from file names is a guess about code nobody read."""
    outcome = await audit(store, gateway, repository, Policy())

    assert len(model.requests) == 2
    choosing = model.requests[0]["messages"][1]["content"]
    reviewing = model.requests[1]["messages"][1]["content"]

    assert "writer.py: write" in choosing, "the first pass gets the outline"
    assert "def write" not in choosing, "and no code"

    assert "=== writer.py ===" in reviewing, "the second pass gets the source"
    assert "def write(path):" in reviewing
    assert "    1 " in reviewing, "with line numbers, so a finding can point at one"
    assert outcome.read == ("writer.py",)


async def test_a_finding_about_a_file_nobody_read_is_dropped(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """The model was shown one file. A finding about another is about nothing."""
    answers(
        model,
        CHOICE,
        json.dumps(
            {
                "findings": [
                    {
                        "file": "reader.py",
                        "line": 1,
                        "title": "not the file we sent",
                        "detail": "x",
                        "severity": "high",
                        "confidence": 0.9,
                    },
                    {
                        "file": "writer.py",
                        "line": 2,
                        "title": "this one was read",
                        "detail": "y",
                        "severity": "low",
                        "confidence": 0.5,
                    },
                ]
            }
        ),
    )
    outcome = await audit(store, gateway, repository, Policy())
    assert [one.file for one in outcome.findings] == ["writer.py"]


async def test_a_file_the_model_invented_is_not_read(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """Asked to name a file, a model will sometimes name one it inferred."""
    answers(
        model,
        json.dumps({"files": [{"path": "does/not/exist.py"}, {"path": "writer.py"}]}),
        ANSWER,
    )
    outcome = await audit(store, gateway, repository, Policy())
    assert outcome.read == ("writer.py",)


async def test_an_audit_that_chooses_nothing_is_a_skip_not_a_failure(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    answers(model, json.dumps({"files": []}), ANSWER)
    outcome = await audit(store, gateway, repository, Policy())
    assert outcome.run.status == "skipped"
    assert outcome.run.reason == "nothing_chosen"


async def test_a_finding_in_a_file_this_run_never_opened_stays_open(
    store: Store, gateway: Gateway, repository: Repository, model: FakeModelServer
) -> None:
    """An audit reads a handful of files, so it settles a handful. Closing a finding
    because nobody looked at it would record it as fixed."""
    from auger.store.findings import Finding, list_findings, record

    record(
        store,
        [
            Finding(
                repo_path=str(repository.path),
                source="audit",
                severity="high",
                title="something in the other file",
                detail="found by an earlier audit",
                file="reader.py",
                line=1,
            )
        ],
    )
    await audit(store, gateway, repository, Policy())
    still = {one.file: one.status for one in list_findings(store, repository.path)}
    assert still["reader.py"] == "open"


async def test_one_long_symbol_is_one_entry_not_several(
    store: Store, repository: Repository
) -> None:
    """A symbol too long for a chunk is stored in parts. Listed separately the parts
    look like several symbols of one name, and an audit calls that a duplicate. It is
    the single largest source of false findings this rig has produced."""
    from auger.context.chunker import Chunk
    from auger.store.index import replace_file

    replace_file(
        store,
        repository.path,
        "long.ts",
        "sha",
        [
            Chunk(
                path="long.ts",
                symbol="handleEmail",
                kind="function",
                start_line=64,
                end_line=189,
                text="x",
            ),
            Chunk(
                path="long.ts",
                symbol="handleEmail part 1",
                kind="function",
                start_line=53,
                end_line=212,
                text="x",
            ),
            Chunk(
                path="long.ts",
                symbol="handleEmail part 2",
                kind="function",
                start_line=205,
                end_line=284,
                text="x",
            ),
        ],
    )
    shape = outline(store, repository.path)
    line = next(one for one in shape.splitlines() if one.startswith("long.ts:"))
    assert line.count("handleEmail") == 1, line
    # The whole span, 53 to 284, not one chunk's 160.
    assert "handleEmail (232)" in line

"""Semgrep reports the pattern, not the problem. Triage is what makes it worth running."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from auger.config import Policy
from auger.config.schema import Backend, Config, ProfileEntry
from auger.jobs.scan_job import run_scan
from auger.jobs.semgrep import command, parse, scan
from auger.jobs.triage import item_text, messages_for, parse_verdicts, triage
from auger.llm import Gateway
from auger.models import Remote, Repository
from auger.net import Allowlist
from auger.sandbox import Network, RunResult, RunSpec, SandboxError
from auger.store import Store
from auger.store.findings import counts, list_findings, record
from tests.helpers import FakeModelServer

Serve = Callable[[object], Awaitable[str]]
REPOSITORY = Repository(path=Path("/x/thing"), remote=Remote("github.com", "acme", "thing"))

OUTPUT = json.dumps(
    {
        "results": [
            {
                "check_id": "python.lang.security.audit.dangerous-subprocess-use",
                "path": "/work/runner.py",
                "start": {"line": 12},
                "extra": {
                    "message": "subprocess call with shell=True",
                    "severity": "ERROR",
                    "lines": "subprocess.run(command, shell=True)",
                    "metadata": {"confidence": "HIGH", "impact": "HIGH"},
                },
            },
            {
                "check_id": "python.lang.best-practice.unspecified-open-encoding",
                "path": "/work/reader.py",
                "start": {"line": 3},
                "extra": {
                    "message": "open() without an encoding",
                    "severity": "WARNING",
                    "lines": "open(path)",
                    "metadata": {"confidence": "MEDIUM"},
                },
            },
        ],
        "errors": [],
    }
)


class StubSandbox:
    """Stands in for the container. The real one has its own tests in M2."""

    name = "stub"

    def __init__(self, result: RunResult | None = None, error: str = "") -> None:
        self.result = result or RunResult("stub", 1, OUTPUT, "", 0.5)
        self.error = error
        self.spec: RunSpec | None = None

    def available(self) -> bool:
        return True

    def run(self, spec: RunSpec) -> RunResult:
        self.spec = spec
        if self.error:
            raise SandboxError(self.error)
        return self.result


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def model() -> FakeModelServer:
    return FakeModelServer()


@pytest.fixture
async def gateway(model: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(model.app())
    config = Config(backend={"triage": Backend(url=f"{base}/v1", model="m")})
    config.profile["balanced"].triage = ProfileEntry(backend="triage")
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


# --- the scan ----------------------------------------------------------------------


def test_the_scan_needs_no_network_at_run_time() -> None:
    """A rule is code, and the rules are vendored into the image."""
    line = command()
    assert line[line.index("--config") + 1] == "/opt/semgrep-security"
    assert "--metrics" in line
    assert line[line.index("--metrics") + 1] == "off"
    assert "--disable-version-check" in line


def test_it_runs_in_the_sandbox_with_no_network() -> None:
    sandbox = StubSandbox()
    scan(sandbox, "/x/thing", "image", "r1")
    assert sandbox.spec is not None
    assert sandbox.spec.network is Network.NONE
    assert sandbox.spec.command[0] == "semgrep"


def test_it_reads_the_results() -> None:
    outcome = parse(OUTPUT, "/x/thing", "r1")
    assert [finding.file for finding in outcome.findings] == ["runner.py", "reader.py"]
    assert outcome.findings[0].line == 12
    assert outcome.findings[0].source == "semgrep"


def test_a_high_impact_error_becomes_critical() -> None:
    outcome = parse(OUTPUT, "/x/thing", "r1")
    assert outcome.findings[0].severity == "critical"
    assert outcome.findings[1].severity == "medium"


def test_the_rule_confidence_reaches_the_finding() -> None:
    outcome = parse(OUTPUT, "/x/thing", "r1")
    assert outcome.findings[0].confidence == 0.8
    assert outcome.findings[1].confidence == 0.6


def test_the_work_prefix_is_stripped_from_the_path() -> None:
    """The path inside the sandbox is not the path the user knows."""
    assert parse(OUTPUT, "/x/thing", "r1").findings[0].file == "runner.py"


def test_a_result_that_names_no_file_is_dropped() -> None:
    body = json.dumps({"results": [{"check_id": "", "path": ""}]})
    assert parse(body, "/x/thing", "r1").findings == []


def test_output_that_is_not_json_is_reported_not_raised() -> None:
    outcome = parse("semgrep crashed", "/x/thing", "r1")
    assert outcome.findings == []
    assert outcome.errors


def test_a_scan_that_will_not_start_is_reported() -> None:
    outcome = scan(StubSandbox(error="no runtime"), "/x/thing", "image", "r1")
    assert outcome.findings == []
    assert outcome.errors == ["no runtime"]


def test_a_scan_that_overruns_is_reported() -> None:
    timed_out = RunResult("stub", 124, "", "", 900.0, timed_out=True)
    outcome = scan(StubSandbox(timed_out), "/x/thing", "image", "r1")
    assert "time limit" in outcome.errors[0]


def test_an_exit_code_of_one_means_it_found_something() -> None:
    """Semgrep exits 1 when it has findings. That is not a failure."""
    outcome = scan(StubSandbox(RunResult("stub", 1, OUTPUT, "", 0.5)), "/x/thing", "i", "r1")
    assert len(outcome.findings) == 2
    assert outcome.errors == []


# --- triage ------------------------------------------------------------------------


def verdicts(entries: list[dict[str, object]]) -> str:
    return json.dumps({"verdicts": entries})


def test_only_the_findings_go_to_the_model() -> None:
    """This is why an audit costs a fraction of a review."""
    findings = parse(OUTPUT, "/x/thing", "r1").findings
    body = messages_for(findings)[1].content
    assert "runner.py:12" in body
    assert "subprocess.run(command, shell=True)" in body
    assert len(body) < 2000


def test_each_item_carries_its_rule_and_its_code() -> None:
    finding = parse(OUTPUT, "/x/thing", "r1").findings[0]
    text = item_text(1, finding)
    assert "dangerous-subprocess-use" in text
    assert "shell=True" in text


def test_it_reads_the_verdicts() -> None:
    found, problems = parse_verdicts(
        verdicts([{"id": 1, "verdict": "false", "reason": "constant"}]), 2
    )
    assert found[1] == ("false", "constant")
    assert problems == []


def test_an_unknown_verdict_is_refused() -> None:
    _, problems = parse_verdicts(verdicts([{"id": 1, "verdict": "maybe"}]), 1)
    assert problems


def test_a_verdict_for_an_item_that_was_not_sent_is_refused() -> None:
    found, problems = parse_verdicts(verdicts([{"id": 99, "verdict": "true"}]), 2)
    assert found == {}
    assert problems


async def test_a_dismissed_finding_leaves_the_list_but_stays_in_the_database(
    store: Store, gateway: Gateway, model: FakeModelServer
) -> None:
    """Deleting it would bring it back on the next scan."""
    findings = parse(OUTPUT, "/x/thing", "r1").findings
    record(store, findings)
    model.reply = verdicts(
        [
            {"id": 1, "verdict": "true", "reason": "the command is user input"},
            {"id": 2, "verdict": "false", "reason": "the file is ascii by construction"},
        ]
    )
    outcome = await triage(store, gateway, findings, Policy())
    assert outcome.real == 1
    assert outcome.dismissed == 1
    assert [finding.file for finding in list_findings(store)] == ["runner.py"]
    assert len(list_findings(store, include_dismissed=True)) == 2


async def test_a_dismissed_finding_is_not_counted(
    store: Store, gateway: Gateway, model: FakeModelServer
) -> None:
    findings = parse(OUTPUT, "/x/thing", "r1").findings
    record(store, findings)
    model.reply = verdicts([{"id": 2, "verdict": "false", "reason": "not affected"}])
    await triage(store, gateway, findings, Policy())
    assert counts(store)["total"] == 1


async def test_the_reason_is_kept_with_the_finding(
    store: Store, gateway: Gateway, model: FakeModelServer
) -> None:
    findings = parse(OUTPUT, "/x/thing", "r1").findings
    record(store, findings)
    model.reply = verdicts([{"id": 1, "verdict": "true", "reason": "the command is user input"}])
    await triage(store, gateway, findings, Policy())
    assert "the command is user input" in list_findings(store)[0].detail


async def test_a_model_that_is_down_leaves_the_findings_showing(
    store: Store, gateway: Gateway
) -> None:
    """An untriaged finding still shows. Losing it would be worse."""
    findings = parse(OUTPUT, "/x/thing", "r1").findings
    record(store, findings)
    gateway.config.backend["triage"].url = "https://api.openai.com/v1"
    outcome = await triage(store, gateway, findings, Policy())
    assert outcome.judged == 0
    assert len(list_findings(store)) == 2


# --- the whole job -----------------------------------------------------------------


async def test_a_scan_run_stores_and_judges(
    store: Store, gateway: Gateway, model: FakeModelServer
) -> None:
    model.reply = verdicts([{"id": 2, "verdict": "false", "reason": "ascii by construction"}])
    outcome = await run_scan(
        store, gateway, StubSandbox(), REPOSITORY, Policy(), "auger/analysis:0.1"
    )
    assert outcome.run.status == "ok"
    assert outcome.run.finding_count == 2
    assert outcome.triage is not None
    assert outcome.triage.dismissed == 1
    assert [finding.file for finding in list_findings(store)] == ["runner.py"]


async def test_a_scan_that_fails_is_recorded_with_its_reason(
    store: Store, gateway: Gateway
) -> None:
    outcome = await run_scan(
        store, gateway, StubSandbox(error="no runtime"), REPOSITORY, Policy(), "image"
    )
    assert outcome.run.status == "failed"
    assert outcome.run.reason == "scan_failed"
    assert outcome.run.error == "no runtime"


async def test_a_clean_scan_calls_no_model(
    store: Store, gateway: Gateway, model: FakeModelServer
) -> None:
    empty = RunResult("stub", 0, json.dumps({"results": [], "errors": []}), "", 0.2)
    outcome = await run_scan(store, gateway, StubSandbox(empty), REPOSITORY, Policy(), "image")
    assert outcome.run.status == "ok"
    assert model.requests == []


def test_a_file_the_parser_cannot_read_is_not_a_failure() -> None:
    """A run that reports a parser limit as an error looks broken when it worked."""
    from auger.jobs.semgrep import parse

    body = json.dumps(
        {
            "results": [],
            "errors": [
                {"message": "Syntax error at line /work/a.sh:13"},
                {"message": "Missing plugin for rule apex-thing"},
                {"message": "could not reach the rule registry"},
            ],
        }
    )
    outcome = parse(body, "/repo", "run-1")
    assert outcome.errors == ["could not reach the rule registry"]
    assert len(outcome.skipped) == 2


def test_the_scan_skips_what_is_not_source() -> None:
    """A repository with four gigabytes of build output in it took half an hour."""
    line = command()
    excluded = {line[index + 1] for index, word in enumerate(line) if word == "--exclude"}
    assert {"node_modules", "target", ".venv", "dist"} <= excluded


def test_one_rule_cannot_decide_how_long_the_scan_takes() -> None:
    line = command()
    assert line[line.index("--timeout") + 1] == "5"
    assert "--timeout-threshold" in line

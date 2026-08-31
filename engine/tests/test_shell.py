"""The command tool, and what the model is told about it.

The notes are asserted against the same values a run uses, because notes that drift are
worse than none: the model plans around limits that are not the real ones.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

from auger.config import Policy
from auger.config.schema import Backend, Config, JobClass, ProfileEntry
from auger.jobs.shell import NAME, Shell
from auger.jobs.tools import complete_with_tools
from auger.llm import Gateway, Message
from auger.net import Allowlist
from auger.sandbox import Network, RunResult, RunSpec, SandboxError, Seatbelt
from tests.helpers import FakeModelServer

Serve = Callable[[object], Awaitable[str]]

IMAGE = "auger/analysis:0.1"
FINDINGS = json.dumps({"findings": []})


class FakeSandbox:
    """Records what it was asked to run, and answers with whatever the test set."""

    name = "fake"

    def __init__(self, result: RunResult | None = None, error: str = "") -> None:
        self.result = result or RunResult(
            backend="fake", exit_code=0, stdout="ok", stderr="", duration_seconds=0.1
        )
        self.error = error
        self.specs: list[RunSpec] = []

    def available(self) -> bool:
        return True

    def run(self, spec: RunSpec) -> RunResult:
        self.specs.append(spec)
        if self.error:
            raise SandboxError(self.error)
        return self.result


def result(exit_code: int = 0, stdout: str = "", stderr: str = "", **kwargs: object) -> RunResult:
    return RunResult(
        backend="fake",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
        **kwargs,  # type: ignore[arg-type]
    )


def shell(sandbox: FakeSandbox, tmp_path: Path, **kwargs: object) -> Shell:
    return Shell(sandbox=sandbox, repository=tmp_path, image=IMAGE, **kwargs)  # type: ignore[arg-type]


# --- what actually runs ----------------------------------------------------------------


async def test_the_command_goes_to_the_sandbox_offline(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    await shell(sandbox, tmp_path).run("pytest -q")
    spec = sandbox.specs[0]
    assert spec.command == ["sh", "-c", "pytest -q"]
    assert spec.network is Network.NONE
    assert spec.repository == tmp_path


async def test_both_streams_and_the_exit_code_come_back(tmp_path: Path) -> None:
    sandbox = FakeSandbox(result(exit_code=2, stdout="out", stderr="bad"))
    text = await shell(sandbox, tmp_path).run("false")
    assert "exit code 2" in text
    assert "out" in text
    assert "bad" in text


async def test_a_silent_command_says_so(tmp_path: Path) -> None:
    assert "printed nothing" in await shell(FakeSandbox(result()), tmp_path).run("true")


async def test_long_output_is_cut(tmp_path: Path) -> None:
    sandbox = FakeSandbox(result(stdout="x" * 5000))
    text = await shell(sandbox, tmp_path, output_limit=100).run("cat log")
    assert "cut after 100 characters" in text
    assert len(text) < 400


async def test_a_timeout_is_reported_and_does_not_raise(tmp_path: Path) -> None:
    sandbox = FakeSandbox(result(exit_code=124, timed_out=True))
    assert "Killed after" in await shell(sandbox, tmp_path).run("sleep 999")


async def test_a_sandbox_that_will_not_start_is_reported_not_raised(tmp_path: Path) -> None:
    sandbox = FakeSandbox(error="no runtime")
    assert "could not run that" in await shell(sandbox, tmp_path).run("true")


# --- what the model is told ------------------------------------------------------------


def test_the_notes_carry_the_real_numbers(tmp_path: Path) -> None:
    notes = shell(FakeSandbox(), tmp_path, timeout_seconds=45.0, output_limit=1234).notes()
    assert "45 seconds" in notes
    assert "1234 characters" in notes


def test_the_notes_say_what_cannot_be_done(tmp_path: Path) -> None:
    notes = shell(FakeSandbox(), tmp_path).notes()
    assert "/work" in notes
    assert "read only" in notes
    assert "no network" in notes.lower()
    assert "Each call starts fresh" in notes


def test_a_degraded_backend_says_it_is_not_a_container(tmp_path: Path) -> None:
    assert "Seatbelt" in Shell(Seatbelt(), tmp_path, IMAGE).notes()
    assert "in a container" in shell(FakeSandbox(), tmp_path).notes()


# --- the tool loop ---------------------------------------------------------------------


@pytest.fixture
def model() -> FakeModelServer:
    return FakeModelServer()


@pytest.fixture
async def gateway(model: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(model.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="m")})
    config.profile["balanced"].review = ProfileEntry(backend="review")
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


def messages() -> list[Message]:
    return [Message("system", "rules"), Message("user", "a diff")]


async def test_the_tool_is_offered_with_no_mcp_server(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    model.reply = FINDINGS
    await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(),
        None,
        shell=shell(FakeSandbox(), tmp_path),
    )
    names = [tool["function"]["name"] for tool in model.requests[0]["tools"]]
    assert names == [NAME]


async def test_the_notes_reach_the_system_message(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    model.reply = FINDINGS
    await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(),
        None,
        shell=shell(FakeSandbox(), tmp_path),
    )
    assert NAME in model.requests[0]["messages"][0]["content"]


async def test_without_a_shell_or_a_server_nothing_is_offered(
    gateway: Gateway, model: FakeModelServer
) -> None:
    model.reply = FINDINGS
    _, run = await complete_with_tools(gateway, None, JobClass.REVIEW, messages(), Policy(), None)
    assert "tools" not in model.requests[0]
    assert run.calls == 0


async def test_a_call_reaches_the_sandbox_and_the_answer_goes_back(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    sandbox = FakeSandbox(result(stdout="3 passed"))
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "pytest -q"})},
        }
    ]
    model.tool_call_rounds = 1
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(),
        None,
        shell=shell(sandbox, tmp_path),
    )
    assert run.calls == 1
    assert sandbox.specs[0].command == ["sh", "-c", "pytest -q"]
    replies = [one for one in model.requests[-1]["messages"] if one["role"] == "tool"]
    assert "3 passed" in replies[0]["content"]


async def test_a_call_with_no_command_is_refused_without_a_run(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    sandbox = FakeSandbox()
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "  "})},
        }
    ]
    model.tool_call_rounds = 1
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(),
        None,
        shell=shell(sandbox, tmp_path),
    )
    assert sandbox.specs == []
    assert run.failed == 1


async def test_the_budget_bounds_the_commands(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    sandbox = FakeSandbox()
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "ls"})},
        }
    ]
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(max_tool_calls=3),
        None,
        shell=shell(sandbox, tmp_path),
    )
    assert run.calls == 3
    assert len(sandbox.specs) == 3


async def test_the_loop_stops_at_the_context_instead_of_overflowing(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """A loop with no ceiling grows the request every turn. The server rejects one over
    its context whole, so the review ends with nothing rather than with less."""
    sandbox = FakeSandbox(result(stdout="x" * 4000))
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "ls"})},
        }
    ]
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(max_tool_calls=0),
        None,
        shell=shell(sandbox, tmp_path),
        budget=5000,
    )
    assert run.truncated
    assert run.calls < 10


async def test_no_budget_leaves_the_ceiling_to_the_policy(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    sandbox = FakeSandbox()
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "ls"})},
        }
    ]
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(max_tool_calls=2),
        None,
        shell=shell(sandbox, tmp_path),
    )
    assert run.calls == 2
    assert not run.truncated


async def test_a_turn_records_what_it_called(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """A tool call carries no text, so without this the transcript shows silence."""
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "ls"})},
        }
    ]
    model.tool_call_rounds = 1
    await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(max_tool_calls=1),
        None,
        shell=shell(FakeSandbox(), tmp_path),
    )
    called = [turn for turn in gateway.transcript if turn.tools]
    assert called and called[0].tools == (NAME,)

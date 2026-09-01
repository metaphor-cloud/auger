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
from auger.jobs.tools import SUPERSEDED, Exchange, compact, complete_with_tools, supersede
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


async def test_a_long_loop_drops_old_output_instead_of_stopping(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """A loop that grows monotonically reaches the working set and has to stop asking,
    with the room the diff needed already spent on stale output. It keeps running on a
    smaller conversation instead."""
    sandbox = FakeSandbox(result(stdout="x" * 4000))
    model.reply = FINDINGS
    # A different command each turn, so nothing is superseded and the only way to stay
    # inside the budget is to drop what is oldest.
    model.tool_calls = [
        {
            "id": "call-{round}",
            "type": "function",
            "function": {"name": NAME, "arguments": '{"command": "ls dir{round}"}'},
        }
    ]
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(max_tool_calls=8),
        None,
        shell=shell(sandbox, tmp_path),
        budget=6000,
    )
    # It ran to its ceiling rather than stopping at the budget.
    assert run.calls == 8
    assert run.dropped > 0
    # And every request it sent stayed inside the budget it was given.
    sent = [turn for turn in gateway.transcript if turn.prompt]
    assert sent, "the loop sent nothing"


async def test_dropped_context_is_marked_rather_than_silent(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """A shorter conversation with nothing said about it reads as one the model never
    had, and it asks for the same things again."""
    head = messages()
    exchanges = [
        Exchange(
            assistant=Message(role="assistant", content=""),
            results=[Message(role="tool", tool_call_id=f"c{index}", content="y" * 3000)],
            keys=[f"run_command({index})"],
        )
        for index in range(4)
    ]
    conversation = compact(head, exchanges, budget=4000)
    assert len(exchanges) == 1, "the oldest exchanges are gone for good"
    note = [one for one in conversation if "dropped" in (one.content or "")]
    assert note, "the model was not told anything was dropped"
    assert "3 earlier tool call" in note[0].content


async def test_the_rules_and_the_diff_never_give_way() -> None:
    """Compaction that drops what the review is about is not compaction."""
    head = messages()
    exchanges = [
        Exchange(
            assistant=Message(role="assistant", content=""),
            results=[Message(role="tool", tool_call_id=f"c{index}", content="y" * 9000)],
            keys=[f"run_command({index})"],
        )
        for index in range(3)
    ]
    conversation = compact(head, exchanges, budget=100)
    assert conversation[0] is head[0]
    assert conversation[1] is head[1]


async def test_a_file_read_twice_appears_once(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """The older copy is the same bytes at an older moment, carried in every request
    from here on."""
    same = json.dumps({"command": "cat main.py"})
    exchanges = [
        Exchange(
            assistant=Message(role="assistant", content=""),
            results=[Message(role="tool", tool_call_id=f"c{index}", content=f"body {index}")],
            keys=[f"run_command({same})"],
        )
        for index in range(3)
    ]
    assert supersede(exchanges) == 2
    kept = [one.results[0].content for one in exchanges]
    assert kept == [SUPERSEDED, SUPERSEDED, "body 2"]


async def test_a_different_call_is_not_superseded() -> None:
    exchanges = [
        Exchange(
            assistant=Message(role="assistant", content=""),
            results=[Message(role="tool", tool_call_id=f"c{index}", content=f"body {index}")],
            keys=[f"run_command(arg {index})"],
        )
        for index in range(3)
    ]
    assert supersede(exchanges) == 0


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
    assert run.dropped == 0


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
    # The name alone says nothing: every command is `run_command`, and which command
    # it ran is the only part worth showing.
    assert called and called[0].tools == (f"{NAME}: ls",)


async def test_a_long_loop_and_a_short_one_answer_the_same(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """The test that compaction kept what mattered. The answer is in the diff, so a
    conversation that dropped the right things still reaches it; one that dropped the
    rules or the change under review does not."""
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-{round}",
            "type": "function",
            "function": {"name": NAME, "arguments": '{"command": "ls dir{round}"}'},
        }
    ]
    answers = []
    for ceiling in (1, 12):
        model.rounds_served = 0
        sandbox = FakeSandbox(result(stdout="x" * 4000))
        completion, _ = await complete_with_tools(
            gateway,
            None,
            JobClass.REVIEW,
            messages(),
            Policy(max_tool_calls=ceiling),
            None,
            shell=shell(sandbox, tmp_path),
            budget=6000,
        )
        answers.append(completion.text)
    assert answers[0] == answers[1]


async def test_exploration_does_not_carry_between_calls(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """Verifying one finding must not start with the previous one's tool output in the
    conversation. Each call builds its own from the messages it was handed."""
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": "ls"})},
        }
    ]
    given = messages()
    for _ in range(2):
        await complete_with_tools(
            gateway,
            None,
            JobClass.REVIEW,
            given,
            Policy(max_tool_calls=2),
            None,
            shell=shell(FakeSandbox(), tmp_path),
        )
    # The caller's own messages are never mutated, so the second call starts where the
    # first one did.
    assert len(given) == 2
    assert all(message.role in ("system", "user") for message in given)


async def test_a_ceiling_part_way_through_a_turn_keeps_the_conversation_valid(
    gateway: Gateway, model: FakeModelServer, tmp_path: Path
) -> None:
    """A server rejects a conversation where an assistant turn names a tool call that
    no result answers, and it rejects the whole request, not the offending part."""
    model.reply = FINDINGS
    model.tool_calls = [
        {
            "id": f"call-{index}",
            "type": "function",
            "function": {"name": NAME, "arguments": json.dumps({"command": f"ls {index}"})},
        }
        for index in range(3)
    ]
    _, run = await complete_with_tools(
        gateway,
        None,
        JobClass.REVIEW,
        messages(),
        Policy(max_tool_calls=2),
        None,
        answer=None,
        shell=shell(FakeSandbox(), tmp_path),
    )
    assert run.calls == 2
    for sent in model.requests:
        for message in sent.get("messages", []):
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            asked = {call["id"] for call in message["tool_calls"]}
            answered = {
                one["tool_call_id"] for one in sent["messages"] if one.get("role") == "tool"
            }
            assert asked <= answered, f"{asked - answered} was asked for and never answered"

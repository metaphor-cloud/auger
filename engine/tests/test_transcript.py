"""The transcript: what the rig said to a model, and what came back."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from auger.llm.transcript import DEPTH, MAX_CHARS, Transcript


def test_it_keeps_the_exchanges_in_order() -> None:
    transcript = Transcript()
    transcript.add("local-review", "gpt", "review", "first", "one")
    transcript.add("local-review", "gpt", "review", "second", "two")
    assert [turn.prompt for turn in transcript] == ["first", "second"]
    assert [turn.id for turn in transcript] == [1, 2]


def test_it_forgets_the_oldest_rather_than_growing_for_ever() -> None:
    """This runs all day. An unbounded log of prompts is the whole codebase in memory."""
    transcript = Transcript()
    for index in range(DEPTH + 10):
        transcript.add("b", "m", "review", f"prompt {index}", "")
    assert len(transcript) == DEPTH
    assert transcript.turns[0].prompt == "prompt 10"


def test_a_long_prompt_is_clipped_and_says_so() -> None:
    transcript = Transcript()
    turn = transcript.add("b", "m", "audit", "x" * (MAX_CHARS + 500), "")
    assert turn.clipped is True
    assert "more characters" in turn.prompt


def test_it_can_be_followed_from_where_you_left_off() -> None:
    transcript = Transcript()
    for index in range(5):
        transcript.add("b", "m", "review", str(index), "")
    assert [turn.prompt for turn in transcript.since(3)] == ["3", "4"]


def test_a_failure_is_recorded_too() -> None:
    """A model that refused is the thing you most want to see."""
    transcript = Transcript()
    turn = transcript.add("b", "m", "review", "p", "", error="connection refused")
    assert turn.error == "connection refused"
    assert turn.answer == ""


async def test_the_route_shows_what_the_model_was_asked(
    http: httpx.AsyncClient, token: str, rig: Any
) -> None:
    rig.gateway.subject = "github.com/acme/alpha"
    rig.gateway.transcript.add(
        "local-review",
        "gpt-oss",
        "review",
        "review this diff",
        "no defects",
        12,
        4,
        900,
        repo=rig.gateway.subject,
    )
    async with http:
        response = await http.get("/transcript", headers={"Authorization": f"Bearer {token}"})
    body = response.json()
    assert response.status_code == 200
    assert body["turns"][0]["prompt"] == "review this diff"
    assert body["turns"][0]["answer"] == "no defects"
    assert body["turns"][0]["repo"] == "github.com/acme/alpha"
    assert body["latest"] == 1


async def test_the_route_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/transcript")).status_code == 401


@pytest.mark.timeout(30)
async def test_a_real_completion_is_recorded(rig: Any, serve: Any) -> None:
    """Against a real server, not a mock of the client."""
    from auger.config.schema import JobClass
    from auger.llm import Message
    from tests.helpers import FakeModelServer

    fake = FakeModelServer()
    fake.reply = '{"findings": []}'
    base = await serve(fake.app())
    (rig.settings.home / "config.toml").write_text(
        f'[backend.local-review]\nurl = "{base}/v1"\nmodel = "test-model"\n', encoding="utf-8"
    )
    rig.reload_config()
    rig.gateway.subject = "github.com/acme/alpha"

    await rig.gateway.complete(JobClass.REVIEW, [Message(role="user", content="is this safe?")])

    turns = rig.gateway.transcript.since(0)
    assert len(turns) == 1
    assert "is this safe?" in turns[0].prompt
    assert turns[0].answer == '{"findings": []}'
    assert turns[0].repo == "github.com/acme/alpha"
    assert turns[0].job_class == "review"


@pytest.mark.timeout(30)
async def test_a_model_that_refuses_is_still_recorded(rig: Any) -> None:
    """A failure is the exchange you most want to look at."""
    from auger.config.schema import JobClass
    from auger.llm import Message, ModelError

    (rig.settings.home / "config.toml").write_text(
        '[backend.local-review]\nurl = "http://127.0.0.1:1/v1"\nmodel = "test"\n',
        encoding="utf-8",
    )
    rig.reload_config()

    with pytest.raises(ModelError):
        await rig.gateway.complete(JobClass.REVIEW, [Message(role="user", content="hello")])

    turns = rig.gateway.transcript.since(0)
    assert len(turns) == 1
    assert turns[0].error
    assert turns[0].answer == ""

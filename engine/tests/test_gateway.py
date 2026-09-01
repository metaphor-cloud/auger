from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

from auger.config.schema import Backend, Config, JobClass, Profile, ProfileEntry
from auger.llm import EgressBlockedError, Gateway, Message, ModelError
from auger.llm.gateway import MINIMUM_ANSWER_TOKENS, MissingBackendError, Usage
from auger.net import Allowlist
from auger.progress import EVERY, Activity
from tests.helpers import FakeModelServer

Serve = Callable[[object], Awaitable[str]]


@pytest.fixture
def fake() -> FakeModelServer:
    return FakeModelServer()


@pytest.fixture
async def gateway(fake: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(fake.app())
    config = Config(
        backend={
            "review": Backend(url=f"{base}/v1", model="review-model"),
            "triage": Backend(url=f"{base}/v1", model="triage-model", max_concurrent=2),
            "embed": Backend(url=f"{base}/v1", model="embed-model"),
            "rerank": Backend(url=f"{base}/v1", model="rerank-model"),
        },
        profile={
            "balanced": Profile(
                triage=ProfileEntry(backend="triage", max_tokens=100, temperature=0.0),
                review=ProfileEntry(backend="review", max_tokens=900, temperature=0.3),
                embed=ProfileEntry(backend="embed"),
                rerank=ProfileEntry(backend="rerank"),
            )
        },
    )
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


HELLO = [Message(role="user", content="hello")]


async def test_a_job_class_chooses_the_backend(gateway: Gateway, fake: FakeModelServer) -> None:
    """A job never names a model. This is the whole point of the profile."""
    review = await gateway.complete(JobClass.REVIEW, HELLO)
    triage = await gateway.complete(JobClass.TRIAGE, HELLO)
    assert review.model == "review-model"
    assert triage.model == "triage-model"
    assert [request["model"] for request in fake.requests] == ["review-model", "triage-model"]


async def test_the_profile_sets_the_limits(gateway: Gateway, fake: FakeModelServer) -> None:
    """900 is below what an answer needs, so it is raised to the floor rather than
    quietly cutting the findings in half. The temperature is passed as written."""
    await gateway.complete(JobClass.REVIEW, HELLO)
    assert fake.requests[0]["max_tokens"] == MINIMUM_ANSWER_TOKENS
    assert fake.requests[0]["temperature"] == 0.3


async def test_a_workable_ceiling_is_passed_as_written(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    gateway.config.profile["balanced"].review = ProfileEntry(backend="review", max_tokens=9000)
    gateway.contexts["review"] = 32768
    await gateway.complete(JobClass.REVIEW, HELLO)
    assert fake.requests[0]["max_tokens"] == 9000


async def test_a_ceiling_above_the_context_is_dropped(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """Past the context it is not a ceiling, it is a number nothing reads. Sending it
    would report a limit that is not the one being enforced."""
    gateway.config.profile["balanced"].review = ProfileEntry(backend="review", max_tokens=200_000)
    gateway.contexts["review"] = 32768
    await gateway.complete(JobClass.REVIEW, HELLO)
    assert "max_tokens" not in fake.requests[0]


async def test_a_bad_ceiling_is_complained_about_once(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """A setting is wrong once, not once per review."""
    gateway.config.profile["balanced"].review = ProfileEntry(backend="review", max_tokens=100)
    await gateway.complete(JobClass.REVIEW, HELLO)
    await gateway.complete(JobClass.REVIEW, HELLO)
    assert len(gateway._said) == 1


async def test_no_ceiling_is_sent_when_the_profile_sets_none(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """`max_tokens` caps what the model writes, and a cap that is too small cuts the
    findings JSON in half. Off is the default, so the field is left out entirely."""
    gateway.config.profile["balanced"].review = ProfileEntry(backend="review")
    await gateway.complete(JobClass.REVIEW, HELLO)
    assert "max_tokens" not in fake.requests[0]


async def test_a_reply_that_stopped_at_the_ceiling_says_so(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """Truncated JSON parses into fewer findings, which looks like a quiet reviewer
    rather than a lost answer."""
    fake.finish_reason = "length"
    assert (await gateway.complete(JobClass.REVIEW, HELLO)).truncated is True
    fake.finish_reason = "stop"
    assert (await gateway.complete(JobClass.REVIEW, HELLO)).truncated is False


async def test_a_changed_profile_changes_the_model_with_no_other_edit(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    gateway.config.profile["balanced"].review = ProfileEntry(backend="triage")
    result = await gateway.complete(JobClass.REVIEW, HELLO)
    assert result.model == "triage-model"


async def test_usage_is_counted_per_backend(gateway: Gateway) -> None:
    await gateway.complete(JobClass.REVIEW, HELLO)
    await gateway.complete(JobClass.REVIEW, HELLO)
    usage = gateway.usage["review"]
    assert usage.requests == 2
    assert usage.prompt_tokens == 22
    assert usage.completion_tokens == 14


async def test_it_retries_a_busy_server(gateway: Gateway, fake: FakeModelServer) -> None:
    fake.fail_times = 2
    fake.fail_status = 503
    result = await gateway.complete(JobClass.REVIEW, HELLO)
    assert result.text == "answer:review-model"
    assert len(fake.requests) == 3


async def test_it_does_not_retry_a_bad_request(gateway: Gateway, fake: FakeModelServer) -> None:
    """A 400 means the request is wrong. Sending it again wastes the model."""
    fake.fail_times = 9
    fake.fail_status = 400
    with pytest.raises(ModelError):
        await gateway.complete(JobClass.REVIEW, HELLO)
    assert len(fake.requests) == 1
    assert gateway.usage["review"].failures == 1


async def test_it_never_exceeds_the_backend_concurrency(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """A continuous batch server stays full, and is never over-committed."""
    fake.delay_seconds = 0.05
    await asyncio.gather(*(gateway.complete(JobClass.TRIAGE, HELLO) for _ in range(8)))
    assert fake.peak_concurrent <= 2
    assert len(fake.requests) == 8


async def test_embeddings_come_back_in_the_order_they_were_sent(gateway: Gateway) -> None:
    """The server may answer out of order. The caller pairs them with its own inputs."""
    vectors = await gateway.embed(["a", "b", "c"])
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


async def test_an_empty_embed_makes_no_request(gateway: Gateway, fake: FakeModelServer) -> None:
    assert await gateway.embed([]) == []
    assert fake.requests == []


async def test_rerank_scores_map_back_to_the_documents(gateway: Gateway) -> None:
    scores = await gateway.rerank("query", ["a", "b", "c"])
    assert scores == [1.0, 0.5, pytest.approx(1 / 3)]


async def test_a_profile_that_names_an_unknown_backend_says_so(gateway: Gateway) -> None:
    gateway.config.profile["balanced"].review = ProfileEntry(backend="ghost")
    with pytest.raises(MissingBackendError, match="ghost"):
        await gateway.complete(JobClass.REVIEW, HELLO)


async def test_an_unknown_profile_says_so(gateway: Gateway) -> None:
    with pytest.raises(MissingBackendError, match="fast"):
        await gateway.complete(JobClass.REVIEW, HELLO, profile="fast")


async def test_the_allowlist_stops_a_backend_that_was_not_approved(
    fake: FakeModelServer, serve: Serve
) -> None:
    """A typo in a backend URL must not send the user's code to a stranger."""
    base = await serve(fake.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="review-model")})
    config.profile["balanced"].review = ProfileEntry(backend="review")
    gateway = Gateway(config, Allowlist())
    try:
        with pytest.raises(EgressBlockedError, match="allowlist"):
            await gateway.complete(JobClass.REVIEW, HELLO)
    finally:
        await gateway.aclose()
    assert fake.requests == []


async def test_a_blocked_backend_is_a_model_error_so_one_run_fails_and_not_the_rig(
    fake: FakeModelServer, serve: Serve
) -> None:
    base = await serve(fake.app())
    config = Config(backend={"review": Backend(url=f"{base}/v1", model="review-model")})
    config.profile["balanced"].review = ProfileEntry(backend="review")
    gateway = Gateway(config, Allowlist())
    try:
        with pytest.raises(ModelError):
            await gateway.complete(JobClass.REVIEW, HELLO)
    finally:
        await gateway.aclose()


async def test_a_large_rerank_is_sent_in_batches(gateway: Gateway, fake: FakeModelServer) -> None:
    """Forty chunks in one call returned 500 from a real server, and the rig retried it
    three times and then lost the whole ordering step."""
    from auger.llm.gateway import RERANK_BATCH

    documents = [f"chunk {index}" for index in range(20)]
    scores = await gateway.rerank("query", documents)
    assert len(scores) == 20
    rerank_calls = [request for request in fake.requests if request["path"] == "/v1/rerank"]
    assert len(rerank_calls) == 3
    assert all(len(call["documents"]) <= RERANK_BATCH for call in rerank_calls)


async def test_each_document_is_trimmed_before_it_is_sent(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """A reranker judges relevance from the head of a chunk. The rest is cost."""
    from auger.llm.gateway import RERANK_DOCUMENT_CHARS

    await gateway.rerank("query", ["x" * (RERANK_DOCUMENT_CHARS * 3)])
    sent = fake.requests[0]["documents"][0]
    assert len(sent) == RERANK_DOCUMENT_CHARS


async def test_the_scores_come_back_in_the_order_the_documents_were_given(
    gateway: Gateway,
) -> None:
    """Batching must not reorder anything: a caller pairs scores with its own list."""
    documents = [f"chunk {index}" for index in range(12)]
    scores = await gateway.rerank("query", documents)
    # The fake scores each batch as 1, 1/2, 1/3, so every batch restarts at 1.0.
    assert scores[0] == 1.0
    assert len(scores) == len(documents)


async def test_an_answer_is_streamed(gateway: Gateway, fake: FakeModelServer) -> None:
    """A local model writes slowly. Waiting for the whole answer before reporting any
    of it is what makes a working rig look like a stalled one."""
    fake.reply = "the whole answer"
    completion = await gateway.complete(JobClass.REVIEW, HELLO)
    assert completion.text == "the whole answer"
    assert fake.requests[0]["stream"] is True
    assert fake.requests[0]["stream_options"] == {"include_usage": True}


async def test_a_streamed_answer_still_reports_what_it_cost(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """Usage arrives in a chunk of its own, after the answer and with no choices in it.
    A reader that stops at the first empty choices list loses every token count."""
    completion = await gateway.complete(JobClass.REVIEW, HELLO)
    assert (completion.prompt_tokens, completion.completion_tokens) == (11, 7)
    assert gateway.usage["review"].prompt_tokens == 11
    assert gateway.usage["review"].completion_tokens == 7


async def test_a_tool_call_split_across_chunks_is_reassembled(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    """A streamed call arrives as a name in one chunk and its arguments a few
    characters at a time. Only the index ties the pieces together."""
    fake.tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "reader.py"}'},
        }
    ]
    fake.tool_call_rounds = 1
    completion = await gateway.complete(JobClass.REVIEW, HELLO, tools=[{"type": "function"}])
    assert [call.name for call in completion.tool_calls] == ["read_file"]
    assert completion.tool_calls[0].arguments == {"path": "reader.py"}
    assert completion.tool_calls[0].id == "call-1"


async def test_a_streamed_answer_that_hit_the_ceiling_says_so(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    fake.finish_reason = "length"
    completion = await gateway.complete(JobClass.REVIEW, HELLO)
    assert completion.truncated


async def test_the_watcher_is_told_the_answer_as_it_arrives(
    gateway: Gateway, fake: FakeModelServer
) -> None:
    said: list[dict[str, object]] = []
    # A clock that moves on every read, so the bounded rate does not hide the reports
    # this test is about. A real answer from a local model takes minutes.
    ticking = itertools.count(1000.0, EVERY).__next__
    activity = Activity(lambda _event, data: said.append(dict(data)), ticking)
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.phase("asking")
    fake.reply = "x" * 200
    await gateway.complete(JobClass.REVIEW, HELLO, watch=watch)
    assert watch.step.tokens > 0, "the count must rise while the answer arrives"
    assert any(data["tokens"] for data in said), "and be published, not only held"
    counts = [int(data["tokens"]) for data in said if data["tokens"]]  # type: ignore[call-overload]
    assert counts == sorted(counts), "and only ever rise"


async def test_embedding_tokens_are_counted(gateway: Gateway) -> None:
    """A backend that only embeds used to read as hundreds of requests and no tokens,
    which says the work was free."""
    await gateway.embed(["one", "two", "three"])
    assert gateway.usage["embed"].requests == 1
    assert gateway.usage["embed"].prompt_tokens == 15


async def test_rerank_tokens_are_counted(gateway: Gateway) -> None:
    await gateway.rerank("query", ["one", "two"])
    assert gateway.usage["rerank"].prompt_tokens == 6


async def test_a_server_that_sends_no_counts_adds_nothing(
    fake: FakeModelServer, serve: Serve
) -> None:
    """A made-up number is worse than an obvious gap."""
    base = await serve(fake.app())
    config = Config(
        backend={"embed": Backend(url=f"{base}/v1", model="embed-model")},
        profile={"balanced": Profile(embed=ProfileEntry(backend="embed"))},
    )
    gateway = Gateway(config, Allowlist.from_values([base]))
    try:
        gateway.usage["embed"] = Usage()
        await gateway.embed(["one"])
        assert gateway.usage["embed"].prompt_tokens == 5
        gateway._counted(gateway.resolve(JobClass.EMBED, "balanced"), {"data": []})
        assert gateway.usage["embed"].prompt_tokens == 5
    finally:
        await gateway.aclose()


async def test_reading_a_large_prompt_is_reported(gateway: Gateway, fake: FakeModelServer) -> None:
    """The longest silence in a real run is a large prompt being read: minutes with no
    token of the answer written yet, inside the phase the answer arrives in."""
    said: list[dict[str, object]] = []
    ticking = itertools.count(1000.0, EVERY).__next__
    activity = Activity(lambda _event, data: said.append(dict(data)), ticking)
    watch = activity.begin("/repo/alpha", "acme/alpha", "diff_review")
    watch.phase("asking")
    fake.prompt_tokens = 40_000
    await gateway.complete(JobClass.REVIEW, HELLO, watch=watch)

    assert fake.requests[0]["return_progress"] is True
    read = [(int(data["done"]), int(data["total"])) for data in said]  # type: ignore[call-overload]
    assert (20_000, 40_000) in read, f"the prompt was never counted: {read}"
    # And once the answer starts, the count is no longer about the prompt.
    assert watch.step.total == 0
    assert watch.step.tokens > 0


async def test_a_hosted_backend_is_not_asked_for_progress(
    fake: FakeModelServer, serve: Serve
) -> None:
    """`return_progress` is a local server's field. A hosted API refuses a field it does
    not know, and a refused request is a lost review."""
    base = await serve(fake.app())
    config = Config(
        backend={"review": Backend(url=f"{base}/v1", model="review-model", hosted=True)},
        profile={"balanced": Profile(review=ProfileEntry(backend="review"))},
    )
    config.egress.allow_hosted = True
    gateway = Gateway(config, Allowlist.from_values([base]))
    try:
        await gateway.complete(JobClass.REVIEW, HELLO)
    finally:
        await gateway.aclose()
    assert "return_progress" not in fake.requests[0]
    assert fake.requests[0]["stream"] is True

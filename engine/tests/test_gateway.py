from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

from reviewrig.config.schema import Backend, Config, JobClass, Profile, ProfileEntry
from reviewrig.llm import EgressBlockedError, Gateway, Message, ModelError
from reviewrig.llm.gateway import MissingBackendError
from reviewrig.net import Allowlist
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
    await gateway.complete(JobClass.REVIEW, HELLO)
    assert fake.requests[0]["max_tokens"] == 900
    assert fake.requests[0]["temperature"] == 0.3


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

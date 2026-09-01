"""The download queue, over real HTTP, with real Range requests.

A pause that loses the bytes is not a pause, so these tests check the file on disk and
not only the state the manager reports.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from auger.downloads import Item, Manager
from auger.net import download
from auger.net.download import PARTIAL, Digest

Serve = Callable[[object], Awaitable[str]]

#: Larger than the megabyte the fetcher reads at a time, several times over, so bytes
#: reach the disk before the transfer ends and a pause can land in the middle of one.
BODY = bytes(range(256)) * 40_000
#: What the server sends per write. Small enough that the delay between them makes the
#: transfer last long enough to interrupt.
CHUNK = 256 * 1024


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git_blob(body: bytes) -> str:
    hasher = hashlib.sha1(usedforsecurity=False)
    hasher.update(b"blob %d\0" % len(body))
    hasher.update(body)
    return hasher.hexdigest()


class Slow:
    """A file server that dribbles bytes and honours a byte range.

    A transfer that finishes in one event loop turn cannot be paused half way, and half
    way is the only interesting place to pause.
    """

    def __init__(self, body: bytes = BODY, delay: float = 0.05) -> None:
        self.body = body
        self.delay = delay
        self.ranges: list[str] = []
        self.honours_range = True

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/model.bin")
        async def model(request: Request) -> Response:
            wanted = request.headers.get("range", "")
            self.ranges.append(wanted)
            start = 0
            if wanted.startswith("bytes=") and self.honours_range:
                start = int(wanted.removeprefix("bytes=").split("-")[0] or 0)
            rest = self.body[start:]

            async def stream() -> Any:
                for at in range(0, len(rest), CHUNK):
                    yield rest[at : at + CHUNK]
                    await asyncio.sleep(self.delay)

            status = 206 if start else 200
            return StreamingResponse(
                stream(),
                status_code=status,
                headers={"content-length": str(len(rest))},
                media_type="application/octet-stream",
            )

        @app.get("/small.json")
        async def small() -> Response:
            return Response(content=b'{"family": "qwen36"}', media_type="application/json")

        return app


@pytest.fixture(autouse=True)
def loopback_is_a_download_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))


@pytest.fixture
def slow() -> Slow:
    return Slow()


def manager(tmp_path: Path, said: list[tuple[str, dict[str, Any]]] | None = None) -> Manager:
    return Manager(tmp_path, publish=None if said is None else lambda e, d: said.append((e, d)))


async def settle(job_state: Callable[[], str], wanted: set[str], timeout: float = 10.0) -> str:
    """Wait until the job reaches one of these states."""
    for _ in range(int(timeout / 0.02)):
        if job_state() in wanted:
            return job_state()
        await asyncio.sleep(0.02)
    raise AssertionError(f"stuck at {job_state()!r}, wanted one of {wanted}")


async def part_grew(path: Path, timeout: float = 10.0) -> int:
    """Wait until the partial file has some bytes in it."""
    for _ in range(int(timeout / 0.02)):
        if path.exists() and path.stat().st_size > 0:
            return path.stat().st_size
        await asyncio.sleep(0.02)
    raise AssertionError(f"{path} never grew")


async def test_a_job_fetches_every_file_and_verifies_each(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    base = await serve(slow.app())
    slow.delay = 0.0
    jobs = manager(tmp_path)
    try:
        job = jobs.submit(
            "a model",
            "weights",
            tmp_path / "a-model",
            [
                Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body))),
                Item(
                    "config.json",
                    f"{base}/small.json",
                    Digest.git_blob(git_blob(b'{"family": "qwen36"}'), 20),
                ),
            ],
        )
        assert await settle(lambda: job.state, {"done", "failed"}) == "done", job.error
    finally:
        await jobs.aclose()
    assert (tmp_path / "a-model" / "weights.bin").read_bytes() == slow.body
    assert (tmp_path / "a-model" / "config.json").read_bytes() == b'{"family": "qwen36"}'
    assert job.received_bytes == job.total_bytes


async def test_a_pause_keeps_the_bytes_and_continuing_asks_only_for_the_rest(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    """The whole point. A dropped transfer of twenty gigabytes that starts again is a
    transfer that never finishes."""
    base = await serve(slow.app())
    jobs = manager(tmp_path)
    partial = tmp_path / "a-model" / f"weights.bin{PARTIAL}"
    try:
        job = jobs.submit(
            "a model",
            "weights",
            tmp_path / "a-model",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        await part_grew(partial)
        jobs.pause(job.id)
        await settle(lambda: job.state, {"paused"})
        held = partial.stat().st_size
        assert 0 < held < len(slow.body), "a pause in the middle, or this proves nothing"

        # Nothing moves while it is paused.
        await asyncio.sleep(0.1)
        assert partial.stat().st_size == held

        slow.delay = 0.0
        jobs.resume(job.id)
        assert await settle(lambda: job.state, {"done", "failed"}) == "done", job.error
    finally:
        await jobs.aclose()

    assert (tmp_path / "a-model" / "weights.bin").read_bytes() == slow.body
    assert not partial.exists()
    # The second request asked for a range starting where the first one stopped.
    assert any(one.startswith("bytes=") for one in slow.ranges), slow.ranges
    resumed = next(one for one in slow.ranges if one.startswith("bytes="))
    assert int(resumed.removeprefix("bytes=").split("-")[0]) > 0


async def test_the_checksum_still_covers_a_file_that_was_paused(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    """A resumed file is hashed over what was already on disk plus what arrives, so a
    corrupt first half is still caught."""
    base = await serve(slow.app())
    jobs = manager(tmp_path)
    partial = tmp_path / "a-model" / f"weights.bin{PARTIAL}"
    try:
        job = jobs.submit(
            "a model",
            "weights",
            tmp_path / "a-model",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        await part_grew(partial)
        jobs.pause(job.id)
        await settle(lambda: job.state, {"paused"})
        # Something else wrote to the partial file while it was stopped.
        with partial.open("r+b") as handle:
            handle.seek(0)
            handle.write(b"\x00" * 64)
        slow.delay = 0.0
        jobs.resume(job.id)
        assert await settle(lambda: job.state, {"done", "failed"}) == "failed"
    finally:
        await jobs.aclose()
    assert "checksum" in job.error
    assert not (tmp_path / "a-model" / "weights.bin").exists()


async def test_cancelling_throws_the_bytes_away(serve: Serve, tmp_path: Path, slow: Slow) -> None:
    """Pause keeps them. Cancel is the one that does not, and the difference has to be
    real or nobody can trust either button."""
    base = await serve(slow.app())
    jobs = manager(tmp_path)
    partial = tmp_path / "a-model" / f"weights.bin{PARTIAL}"
    try:
        job = jobs.submit(
            "a model",
            "weights",
            tmp_path / "a-model",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        await part_grew(partial)
        jobs.cancel(job.id)
        await settle(lambda: job.state, {"cancelled"})
        await asyncio.sleep(0.05)
    finally:
        await jobs.aclose()
    assert not partial.exists()
    assert not (tmp_path / "a-model" / "weights.bin").exists()


async def test_one_job_runs_at_a_time_and_the_next_follows(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    base = await serve(slow.app())
    slow.delay = 0.0
    jobs = manager(tmp_path)
    try:
        first = jobs.submit(
            "first",
            "weights",
            tmp_path / "first",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)))],
        )
        second = jobs.submit(
            "second",
            "weights",
            tmp_path / "second",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)))],
        )
        assert second.state == "queued"
        assert await settle(lambda: first.state, {"done", "failed"}) == "done", first.error
        assert await settle(lambda: second.state, {"done", "failed"}) == "done", second.error
    finally:
        await jobs.aclose()


async def test_a_paused_job_does_not_block_the_queue(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    base = await serve(slow.app())
    jobs = manager(tmp_path)
    try:
        first = jobs.submit(
            "first",
            "weights",
            tmp_path / "first",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        second = jobs.submit(
            "second",
            "weights",
            tmp_path / "second",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        await part_grew(tmp_path / "first" / f"weights.bin{PARTIAL}")
        slow.delay = 0.0
        jobs.pause(first.id)
        assert await settle(lambda: second.state, {"done", "failed"}) == "done", second.error
        assert first.state == "paused"
    finally:
        await jobs.aclose()


async def test_asking_twice_continues_the_same_job(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    """Two transfers into one file is a corrupt file, so a second request for the same
    thing is the first one again."""
    base = await serve(slow.app())
    jobs = manager(tmp_path)
    try:
        items = [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))]
        first = jobs.submit("a model", "weights", tmp_path / "a-model", items)
        await part_grew(tmp_path / "a-model" / f"weights.bin{PARTIAL}")
        jobs.pause(first.id)
        await settle(lambda: first.state, {"paused"})
        slow.delay = 0.0
        again = jobs.submit("a model", "weights", tmp_path / "a-model", list(items))
        assert again.id == first.id, "a second ask must not start a rival transfer"
        assert await settle(lambda: first.state, {"done", "failed"}) == "done", first.error
        assert len(jobs.jobs()) == 1
    finally:
        await jobs.aclose()


async def test_progress_is_published_but_not_per_chunk(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    base = await serve(slow.app())
    slow.delay = 0.0
    said: list[tuple[str, dict[str, Any]]] = []
    jobs = manager(tmp_path, said)
    try:
        job = jobs.submit(
            "a model",
            "weights",
            tmp_path / "a-model",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        assert await settle(lambda: job.state, {"done", "failed"}) == "done", job.error
    finally:
        await jobs.aclose()
    assert [event for event, _ in said] == ["download.changed"] * len(said)
    states = [data["state"] for _, data in said]
    assert states[0] == "queued"
    assert states[-1] == "done"
    chunks = len(slow.body) // CHUNK
    assert len(said) < chunks, f"{len(said)} events for {chunks} chunks is one per chunk"


async def test_a_server_that_ignores_the_range_starts_again_rather_than_corrupt_the_file(
    serve: Serve, tmp_path: Path, slow: Slow
) -> None:
    base = await serve(slow.app())
    jobs = manager(tmp_path)
    partial = tmp_path / "a-model" / f"weights.bin{PARTIAL}"
    try:
        job = jobs.submit(
            "a model",
            "weights",
            tmp_path / "a-model",
            [Item("weights.bin", f"{base}/model.bin", Digest.sha256(sha(slow.body)), len(BODY))],
        )
        await part_grew(partial)
        jobs.pause(job.id)
        await settle(lambda: job.state, {"paused"})
        slow.honours_range = False
        slow.delay = 0.0
        jobs.resume(job.id)
        assert await settle(lambda: job.state, {"done", "failed"}) == "done", job.error
    finally:
        await jobs.aclose()
    assert (tmp_path / "a-model" / "weights.bin").read_bytes() == slow.body

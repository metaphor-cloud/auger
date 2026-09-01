"""The queue of things being fetched, and the controls on it.

A model is tens of gigabytes, sometimes hundreds, and sometimes forty files rather than
one. A transfer that size is not an event, it is a state: it outlives the click that
started it, the user wants their bandwidth back in the middle of it, and they want it to
carry on afterwards rather than start again.

Resuming is already the fetcher's job: it asks for the missing byte range and re-hashes
what is on disk, so a checksum still covers the whole file after a pause. This module is
what turns that into something with a stop button - a job per thing, a file at a time
within it, and one place that knows what is in flight.

The queue lives in memory. The bytes do not: a restart loses the list and keeps every
partial file, so asking for the same model again continues where it stopped. That is
worth saying out loud rather than implying the queue is durable.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from auger.log import Logger, create_logger
from auger.net.download import PARTIAL, Digest, DownloadError, Progress, fetch

#: How often progress is published while bytes move. A megabyte chunk on a fast link is
#: a hundred callbacks a second, and nobody reads a hundred.
EVERY = 0.4

#: What a job is for. The window groups by this, and the runtime jobs are the ones that
#: must finish before anything else is any use.
KINDS = ("runtime", "weights")

_ids = itertools.count(1)


@dataclass
class Item:
    """One file in a job."""

    #: Where it lands, relative to the job's directory. A repository of shards keeps its
    #: own layout, so this can contain a separator.
    name: str
    url: str
    checksum: Digest
    size_bytes: int = 0
    received: int = 0
    done: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "received": self.received,
            "done": self.done,
        }


@dataclass
class Job:
    """One thing being fetched, in as many files as it takes."""

    id: str
    label: str
    kind: str
    destination: Path
    items: list[Item]
    state: str = "queued"
    error: str = ""
    started: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    #: Bytes moved since this job last started or continued, and when that was, so a
    #: rate can be worked out without counting what a previous run already had.
    moved: int = 0
    moving_since: float = 0.0

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.items)

    @property
    def received_bytes(self) -> int:
        return sum(item.size_bytes if item.done else item.received for item in self.items)

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "destination": str(self.destination),
            "state": self.state,
            "error": self.error,
            "started": self.started,
            "updated": self.updated,
            "total_bytes": self.total_bytes,
            "received_bytes": self.received_bytes,
            "moved": self.moved,
            "moving_since": self.moving_since,
            "files": len(self.items),
            "files_done": sum(1 for item in self.items if item.done),
            "current": next((item.name for item in self.items if not item.done), ""),
            "items": [item.as_dict() for item in self.items],
        }


Publish = Callable[[str, dict[str, Any]], None]
Watcher = Callable[[Job], None]


class Manager:
    """Every download, and the controls on them.

    One job runs at a time. Two large transfers over one link finish no sooner than one
    after the other and each reports a rate that means nothing, so the queue is a queue.
    """

    def __init__(
        self,
        home: Path,
        publish: Publish | None = None,
        token: Callable[[], str | None] | None = None,
        log: Logger | None = None,
    ) -> None:
        self.home = home
        self._publish = publish
        self._token = token or (lambda: None)
        self.log = (log or create_logger("download")).bind(component="downloads")
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._watchers: dict[str, Watcher] = {}
        self._running: asyncio.Task[None] | None = None
        self._current: str = ""
        self._said = 0.0
        #: Set when a job reaches an end, so a caller that needs the bytes before it can
        #: carry on has something to wait for.
        self._over: dict[str, asyncio.Event] = {}

    # --- the list ------------------------------------------------------------------

    def jobs(self) -> list[Job]:
        return [self._jobs[key] for key in self._order if key in self._jobs]

    def job(self, key: str) -> Job | None:
        return self._jobs.get(key)

    def find(self, label: str, kind: str) -> Job | None:
        """A job for the same thing that has not finished, so a second click on the
        same model continues it rather than starting a rival transfer."""
        for job in self.jobs():
            if job.label == label and job.kind == kind and not job.finished:
                return job
        return None

    def submit(
        self,
        label: str,
        kind: str,
        destination: Path,
        items: list[Item],
        watcher: Watcher | None = None,
    ) -> Job:
        """Add a job and start working the queue. An identical job in flight is returned
        as it is, because two transfers into one file is a corrupt file."""
        existing = self.find(label, kind)
        if existing is not None:
            if watcher is not None:
                self._watchers[existing.id] = watcher
            if existing.state == "paused":
                self.resume(existing.id)
            return existing
        job = Job(
            id=f"d{next(_ids)}",
            label=label,
            kind=kind,
            destination=destination,
            items=items,
        )
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._over[job.id] = asyncio.Event()
        if watcher is not None:
            self._watchers[job.id] = watcher
        self.log.info(
            "download queued",
            job=job.id,
            label=label,
            kind=kind,
            files=len(items),
            bytes=job.total_bytes,
        )
        self._say(job, force=True)
        self._pump()
        return job

    # --- the controls --------------------------------------------------------------

    def pause(self, key: str) -> Job | None:
        """Stop moving bytes and keep every one already written."""
        job = self._jobs.get(key)
        if job is None or job.finished or job.state == "paused":
            return job
        job.state = "paused"
        job.moving_since = 0.0
        if self._current == key and self._running is not None:
            # Cancelling the task leaves the partial file where it is: the fetcher only
            # deletes one on a failure, and a cancellation is not one.
            self._running.cancel()
        self.log.info("download paused", job=job.id, label=job.label)
        self._say(job, force=True)
        return job

    def resume(self, key: str) -> Job | None:
        job = self._jobs.get(key)
        if job is None or job.finished or job.state == "running":
            return job
        job.state = "queued"
        self.log.info("download continued", job=job.id, label=job.label)
        self._say(job, force=True)
        self._pump()
        return job

    def cancel(self, key: str) -> Job | None:
        """Drop the job and the bytes. This is the one that throws work away."""
        job = self._jobs.get(key)
        if job is None:
            return None
        was = job.state
        job.state = "cancelled"
        job.moving_since = 0.0
        if self._current == key and self._running is not None:
            self._running.cancel()
        if was != "done":
            self._clear_partials(job)
        self.log.info("download cancelled", job=job.id, label=job.label)
        self._say(job, force=True)
        self._pump()
        return job

    def forget(self, key: str) -> None:
        """Take a finished job off the list."""
        job = self._jobs.get(key)
        if job is None or not job.finished:
            return
        del self._jobs[key]
        self._watchers.pop(key, None)
        self._over.pop(key, None)
        self._order.remove(key)

    async def wait(self, key: str) -> Job | None:
        """Wait until a job is done, failed or cancelled.

        A paused job is still waited on: the user stopped it and may continue it, and
        guessing on their behalf is worse than waiting. Cancel is how a caller gets out.
        """
        job = self._jobs.get(key)
        if job is None:
            return None
        over = self._over.get(key)
        if over is None:
            return job
        await over.wait()
        return job

    async def aclose(self) -> None:
        if self._running is not None:
            self._running.cancel()
            await asyncio.gather(self._running, return_exceptions=True)
            self._running = None

    # --- the work ------------------------------------------------------------------

    def _next(self) -> Job | None:
        return next((job for job in self.jobs() if job.state == "queued"), None)

    def _pump(self) -> None:
        if self._running is not None and not self._running.done():
            return
        job = self._next()
        if job is None:
            self._current = ""
            return
        self._current = job.id
        self._running = asyncio.create_task(self._work(job), name=f"auger-download-{job.id}")

    async def _work(self, job: Job) -> None:
        job.state = "running"
        job.moved = 0
        job.moving_since = time.time()
        self._say(job, force=True)
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as http:
                for item in job.items:
                    if item.done:
                        continue
                    await self._one(http, job, item)
            job.state = "done"
            job.moving_since = 0.0
            self.log.info("download finished", job=job.id, label=job.label)
        except asyncio.CancelledError:
            # Paused or cancelled. Both already set the state, and the partial files are
            # handled by whichever one it was.
            if job.state == "running":
                job.state = "paused"
            raise
        except DownloadError as error:
            job.state = "failed"
            job.error = str(error)
            job.moving_since = 0.0
            self.log.error(
                "download failed", reason="fetch_failed", job=job.id, label=job.label, error=error
            )
        except (httpx.HTTPError, OSError) as error:
            job.state = "failed"
            job.error = str(error)
            job.moving_since = 0.0
            self.log.error(
                "download failed", reason="transport", job=job.id, label=job.label, error=error
            )
        finally:
            self._say(job, force=True)
            self._running = None
            self._current = ""
            # Always: a paused job is no longer queued, so the next one is free to run
            # rather than waiting behind something nobody intends to continue yet.
            self._pump()

    async def _one(self, http: httpx.AsyncClient, job: Job, item: Item) -> None:
        target = job.destination / item.name
        # What the last callback said, so the rate counts each byte once. A resumed file
        # starts its first callback well above zero.
        seen = item.received

        def report(progress: Progress) -> None:
            nonlocal seen
            item.received = progress.received_bytes
            if progress.total_bytes and not item.size_bytes:
                item.size_bytes = progress.total_bytes
            job.moved += max(0, progress.received_bytes - seen)
            seen = progress.received_bytes
            self._say(job)

        await fetch(http, item.url, target, item.checksum, report, self.log, self._token())
        item.done = True
        item.received = item.size_bytes or item.received
        self._say(job, force=True)

    def _clear_partials(self, job: Job) -> None:
        for item in job.items:
            target = job.destination / item.name
            target.with_suffix(target.suffix + PARTIAL).unlink(missing_ok=True)
            item.received = 0

    def _say(self, job: Job, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._said < EVERY:
            return
        self._said = now
        job.updated = now
        if job.finished:
            over = self._over.get(job.id)
            if over is not None:
                over.set()
        watcher = self._watchers.get(job.id)
        if watcher is not None:
            watcher(job)
        if self._publish is not None:
            self._publish("download.changed", job.as_dict())

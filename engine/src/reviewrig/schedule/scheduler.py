"""The work queue.

Priority decides the order. A repository never has two reviews at once, and the machine
never has more than the configured number in total, so a scan of a hundred repositories
does not starve the machine the user is working on.

A repository that is busy is put back, not dropped, and the skip is recorded with its
reason. A repository that is never reviewed must be visible.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from pathlib import Path

from reviewrig.config import Policy
from reviewrig.forge import PullRequest
from reviewrig.jobs import JobOutcome, diff_review, pr_review
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository
from reviewrig.schedule.protocol import RigLike
from reviewrig.store.runs import record_skip
from reviewrig.watch import busy

_sequence = itertools.count()


@dataclass(order=True)
class Task:
    """One piece of work. Ordered by priority, then by arrival."""

    priority: int
    sequence: int = field(default_factory=lambda: next(_sequence))
    repository: Repository = field(compare=False, default=None)  # type: ignore[assignment]
    policy: Policy = field(compare=False, default=None)  # type: ignore[assignment]
    kind: str = field(compare=False, default=diff_review.KIND)
    base: str | None = field(compare=False, default=None)
    target: str = field(compare=False, default="HEAD")
    attempts: int = field(compare=False, default=0)
    pull: PullRequest | None = field(compare=False, default=None)

    @classmethod
    def for_pull(cls, repository: Repository, policy: Policy, pull: PullRequest) -> Task:
        return cls(
            priority=policy.priority,
            repository=repository,
            policy=policy,
            kind=pr_review.KIND,
            target=f"pull/{pull.number}",
            pull=pull,
        )

    @classmethod
    def review(
        cls,
        repository: Repository,
        policy: Policy,
        base: str | None = None,
        target: str = "HEAD",
    ) -> Task:
        return cls(
            priority=policy.priority,
            repository=repository,
            policy=policy,
            base=base,
            target=target,
        )


class Scheduler:
    def __init__(self, rig: RigLike, log: Logger | None = None) -> None:
        self.rig = rig
        self.log = (log or create_logger("schedule")).bind(component="schedule")
        self.queue: asyncio.PriorityQueue[Task] = asyncio.PriorityQueue()
        self._workers: list[asyncio.Task[None]] = []
        self._deferred: set[asyncio.Task[None]] = set()
        self._in_flight: set[Path] = set()
        self._queued: set[tuple[Path, str, str]] = set()
        self._resumed = asyncio.Event()
        self._resumed.set()
        self.running = False

    # --- lifecycle ---------------------------------------------------------------

    async def start(self, workers: int) -> None:
        if self.running:
            return
        self.running = True
        self._workers = [
            asyncio.create_task(self._work(index), name=f"reviewrig-worker-{index}")
            for index in range(workers)
        ]
        self.log.info("scheduler started", workers=workers)

    async def stop(self) -> None:
        self.running = False
        for task in [*self._workers, *self._deferred]:
            task.cancel()
        await asyncio.gather(*self._workers, *self._deferred, return_exceptions=True)
        self._workers.clear()
        self._deferred.clear()
        self.log.info("scheduler stopped")

    # --- submission --------------------------------------------------------------

    def key(self, task: Task) -> tuple[Path, str, str]:
        return (task.repository.path, task.kind, task.target)

    def submit(self, task: Task) -> bool:
        """Queue a task. Returns False when the same work is already waiting."""
        key = self.key(task)
        if key in self._queued:
            return False
        self._queued.add(key)
        self.queue.put_nowait(task)
        return True

    def _defer(self, task: Task, seconds: float) -> None:
        """Put a task back after a wait, without holding a worker."""

        async def later() -> None:
            try:
                await asyncio.sleep(seconds)
                self.queue.put_nowait(task)
            except asyncio.CancelledError:
                self._queued.discard(self.key(task))
                raise

        handle = asyncio.create_task(later())
        self._deferred.add(handle)
        handle.add_done_callback(self._deferred.discard)

    @property
    def pending(self) -> int:
        return self.queue.qsize() + len(self._deferred)

    @property
    def in_flight(self) -> list[str]:
        return sorted(str(path) for path in self._in_flight)

    @property
    def paused(self) -> bool:
        return not self._resumed.is_set()

    def pause(self) -> None:
        """Finish what is running, then stop pulling. Queued work waits."""
        self._resumed.clear()
        self.log.info("scheduler paused", pending=self.pending)

    def resume(self) -> None:
        self._resumed.set()
        self.log.info("scheduler resumed", pending=self.pending)

    # --- the worker loop ---------------------------------------------------------

    async def _work(self, index: int) -> None:
        while True:
            await self._resumed.wait()
            task = await self.queue.get()
            try:
                await self._run(task)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.log.error(
                    "task crashed",
                    reason="task_error",
                    repo=task.repository.slug,
                    error=error,
                )
            finally:
                self.queue.task_done()

    async def _execute(self, task: Task) -> JobOutcome | None:
        rig = self.rig
        if task.kind == pr_review.KIND and task.pull is not None:
            found = rig.forges.for_repository(task.repository)
            if found is None:
                self.log.warn(
                    "pull request skipped",
                    reason="no_forge",
                    repo=task.repository.slug,
                )
                return None
            entry, repo = found
            return await pr_review.review_pull(
                store=rig.store,
                gateway=rig.gateway,
                entry=entry,
                repo=repo,
                pull=task.pull,
                repository=task.repository,
                policy=task.policy,
                tools=rig.tools,
                log=self.log,
            )
        return await diff_review.review(
            store=rig.store,
            gateway=rig.gateway,
            repository=task.repository,
            policy=task.policy,
            base=task.base,
            target=task.target,
            tools=rig.tools,
            log=self.log,
        )

    async def _run(self, task: Task) -> None:
        rig = self.rig
        path = task.repository.path
        if path in self._in_flight:
            # Another worker holds this repository. Come back to it.
            self._defer(task, 5.0)
            return

        state = await asyncio.to_thread(
            busy.check, path, task.policy.idle_seconds, busy.AGENT_NAMES, self.log
        )
        if state.busy:
            self._queued.discard(self.key(task))
            await asyncio.to_thread(
                record_skip, rig.store, path, task.kind, state.reason or "busy", state.detail
            )
            self.log.info(
                "repository skipped",
                reason=state.reason,
                repo=task.repository.slug,
                detail=state.detail,
            )
            rig.publish(
                "run.skipped",
                repo=str(path),
                slug=task.repository.slug,
                reason=state.reason,
                detail=state.detail,
            )
            task.attempts += 1
            self._queued.add(self.key(task))
            self._defer(task, float(rig.config.schedule.retry_seconds))
            return

        self._in_flight.add(path)
        self._queued.discard(self.key(task))
        rig.publish("run.started", repo=str(path), slug=task.repository.slug, kind=task.kind)
        try:
            outcome = await self._execute(task)
        finally:
            self._in_flight.discard(path)
        if outcome is None:
            return

        rig.publish(
            "run.finished",
            repo=str(path),
            slug=task.repository.slug,
            run=outcome.run.id,
            status=outcome.run.status,
            findings=outcome.run.finding_count,
            reason=outcome.run.reason,
        )
        for finding in outcome.findings:
            rig.publish(
                "finding.new",
                repo=str(path),
                slug=task.repository.slug,
                severity=finding.severity,
                title=finding.title,
                file=finding.file,
            )

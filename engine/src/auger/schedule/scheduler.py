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

from auger.config import Policy
from auger.config.schema import JobClass
from auger.forge import PullRequest
from auger.jobs import JobOutcome, audit, diff_review, pr_review
from auger.jobs.shell import Shell
from auger.log import Logger, create_logger
from auger.models import Repository
from auger.schedule.protocol import RigLike
from auger.store.runs import record_skip
from auger.watch import busy, idle

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
    def for_audit(cls, repository: Repository, policy: Policy) -> Task:
        # An audit reads a whole repository. It waits behind every change.
        return cls(
            priority=9,
            repository=repository,
            policy=policy,
            kind=audit.KIND,
            target="audit",
        )

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
            asyncio.create_task(self._work(index), name=f"auger-worker-{index}")
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

    def _backend_for(self, task: Task) -> str:
        """The backend that has to answer for this task, or empty if none does.

        A job never names a model, so this asks the same question the job will: the
        profile decides, and the kind of work decides which entry of it.
        """
        wanted = JobClass.REVIEW
        profile = self.rig.config.profile.get(task.policy.model_profile)
        return profile.entry(wanted).backend if profile else ""

    async def _model_ready(self, task: Task) -> str | None:
        """Start the backend this task needs. The reason it cannot run, or None.

        Without this the task runs against a server that is not there and is recorded
        as a failed review. It is not a failed review. It is one that never happened.
        """
        if self.rig.verifying:
            # The second model holds the memory while it judges. Starting the reviewer
            # now would put two large models in the same machine.
            return "model_busy"
        name = self._backend_for(task)
        if not name:
            return None
        # This probes, and starts the server when the rig owns it. A hand-run or
        # hosted one it cannot start, and a task that would only fail waits instead.
        health = await self.rig.ensure_backend(name)
        return None if health.up else "model_down"

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
                shell=self._shell(task),
            )
        if task.kind == audit.KIND:
            return await audit.audit(
                store=rig.store,
                gateway=rig.gateway,
                repository=task.repository,
                policy=task.policy,
                log=self.log,
                sandbox=rig.selection.sandbox,
                image=rig.config.image,
            )
        return await diff_review.review(
            store=rig.store,
            gateway=rig.gateway,
            repository=task.repository,
            policy=task.policy,
            base=task.base,
            target=task.target,
            tools=rig.tools,
            graph=rig.config.codegraph,
            log=self.log,
            shell=self._shell(task),
        )

    def _shell(self, task: Task) -> Shell | None:
        """The sandbox, as a tool the reviewer may call.

        Only when the repository asked for one. A command tool turns every review into
        an agentic loop whose turns each cost a container start, and the deterministic
        retrieval path is both faster and the one that finishes.

        No image means no analysis image was built, and a command with nothing to run
        in is worse than no command at all: the model would spend its budget on calls
        that cannot work.
        """
        rig = self.rig
        if not task.policy.commands or not rig.config.image:
            return None
        return Shell(
            sandbox=rig.selection.sandbox,
            repository=task.repository.path,
            image=rig.config.image,
        )

    async def _run(self, task: Task) -> None:
        rig = self.rig
        path = task.repository.path
        if path in self._in_flight:
            # Another worker holds this repository. Come back to it.
            self._defer(task, 5.0)
            return

        schedule = rig.config.schedule
        if schedule.idle_only:
            machine = await asyncio.to_thread(idle.current)
            if not machine.free_for(schedule.idle_after_seconds):
                # Put it back rather than dropping it. The machine will be free later,
                # and a review nobody sees is still a review that has to happen.
                self._queued.discard(self.key(task))
                await asyncio.to_thread(
                    record_skip,
                    rig.store,
                    path,
                    task.kind,
                    "machine_in_use",
                    f"idle for {machine.seconds:.0f}s of {schedule.idle_after_seconds}s",
                )
                rig.publish(
                    "run.skipped",
                    repo=str(path),
                    slug=task.repository.slug,
                    reason="machine_in_use",
                    detail=f"{machine.seconds:.0f}s idle",
                )
                task.attempts += 1
                self._queued.add(self.key(task))
                self._defer(task, float(schedule.retry_seconds))
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

        blocked = await self._model_ready(task)
        if blocked is not None:
            self._queued.discard(self.key(task))
            detail = f"{self._backend_for(task)} is not up"
            await asyncio.to_thread(record_skip, rig.store, path, task.kind, blocked, detail)
            self.log.warn(
                "run held back",
                reason=blocked,
                repo=task.repository.slug,
                backend=self._backend_for(task),
            )
            rig.publish(
                "run.skipped",
                repo=str(path),
                slug=task.repository.slug,
                reason=blocked,
                detail=detail,
            )
            task.attempts += 1
            self._queued.add(self.key(task))
            self._defer(task, float(rig.config.schedule.retry_seconds))
            return

        self._in_flight.add(path)
        self._queued.discard(self.key(task))
        # The transcript labels each exchange with the work it belongs to.
        rig.gateway.subject = task.repository.slug
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

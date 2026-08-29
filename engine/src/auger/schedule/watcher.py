"""Notice new work.

The watcher compares each repository's HEAD with the commit that was last reviewed. When
they differ, it queues a review of exactly the range between them, so a repository that
gained ten commits overnight is reviewed once, not ten times.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from auger.forge import ForgeError
from auger.log import Logger, create_logger
from auger.schedule.protocol import RigLike
from auger.schedule.quiet import is_quiet
from auger.schedule.scheduler import Scheduler, Task
from auger.store.runs import last_audit, pull_reviewed, reviewed_head
from auger.watch import git


def due(rig: RigLike, log: Logger) -> list[Task]:
    """Tasks for every enabled repository whose HEAD moved since its last review."""
    tasks: list[Task] = []
    for view in rig.repositories():
        policy = view.policy
        if not policy.enabled or policy.mode == "off":
            continue
        path = view.repository.path
        if not path.is_dir():
            continue
        try:
            head = git.head(path)
        except git.GitError as error:
            log.warn("head unreadable", reason="git_failed", repo=view.repository.slug, error=error)
            continue
        last = reviewed_head(rig.store, path)
        if last == head:
            continue
        tasks.append(Task.review(view.repository, policy, base=last, target=head))
    return tasks


async def poll_once(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> int:
    """Queue every repository that has something new. Returns how many were queued."""
    log = log or create_logger("schedule")
    tasks = await asyncio.to_thread(due, rig, log)
    queued = sum(1 for task in tasks if scheduler.submit(task))
    if queued:
        log.info("queued reviews", count=queued, pending=scheduler.pending)
        rig.publish("queue.changed", pending=scheduler.pending)
    return queued


async def watch(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> None:
    """Poll forever. Cancel the task to stop."""
    log = (log or create_logger("schedule")).bind(component="watcher")
    while True:
        try:
            await poll_once(rig, scheduler, log)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("watcher cycle failed", reason="watcher_error", error=error)
        await asyncio.sleep(rig.config.schedule.poll_seconds)


async def poll_pull_requests(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> int:
    """Queue a review for every pull request that the policy says the rig should read.

    A pull request whose head has already been reviewed is left alone, so a poll costs
    nothing while nothing changes.
    """
    log = (log or create_logger("schedule")).bind(component="watcher")
    await rig.forges.refresh_users()
    queued = 0
    for view in rig.repositories():
        policy = view.policy
        if not policy.enabled or policy.mode == "off":
            continue
        found = rig.forges.for_repository(view.repository)
        if found is None:
            continue
        entry, repo = found
        try:
            pulls = await entry.forge.pull_requests(repo)
        except ForgeError as error:
            log.warn(
                "pull requests unreadable",
                reason="forge_failed",
                repo=view.repository.slug,
                error=error,
            )
            continue
        for pull in pulls:
            if pull.draft:
                continue  # A draft pull request is not ready for a reviewer.
            if policy.auto_review_assigned_prs and not pull.concerns(entry.state.user):
                continue
            if pull_reviewed(rig.store, view.repository.path, pull.head_sha):
                continue
            if scheduler.submit(Task.for_pull(view.repository, policy, pull)):
                queued += 1
    if queued:
        log.info("queued pull request reviews", count=queued, pending=scheduler.pending)
        rig.publish("queue.changed", pending=scheduler.pending)
    return queued


async def watch_forges(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> None:
    """Poll the forges forever. Slower than the local poll, because a forge counts."""
    log = (log or create_logger("schedule")).bind(component="watcher")
    while True:
        try:
            if rig.forges.entries:
                await poll_pull_requests(rig, scheduler, log)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("forge cycle failed", reason="watcher_error", error=error)
        await asyncio.sleep(rig.config.schedule.forge_poll_seconds)


def audit_due(rig: RigLike, log: Logger, now: datetime | None = None) -> list[Task]:
    """Every repository whose last audit is older than its policy allows."""
    moment = now or datetime.now(UTC)
    tasks: list[Task] = []
    for view in rig.repositories():
        policy = view.policy
        if not policy.enabled or policy.mode == "off" or policy.audit_hours <= 0:
            continue
        last = last_audit(rig.store, view.repository.path)
        if last:
            try:
                when = datetime.fromisoformat(last)
            except ValueError:
                when = None
            if when and moment - when < timedelta(hours=policy.audit_hours):
                continue
        tasks.append(Task.for_audit(view.repository, policy))
    return tasks


async def poll_audits(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> int:
    """Queue the audits that are due, unless the user asked for quiet."""
    log = (log or create_logger("schedule")).bind(component="watcher")
    if is_quiet(rig.config.schedule.quiet_hours):
        return 0
    tasks = await asyncio.to_thread(audit_due, rig, log)
    queued = sum(1 for task in tasks if scheduler.submit(task))
    if queued:
        log.info("queued audits", count=queued, pending=scheduler.pending)
        rig.publish("queue.changed", pending=scheduler.pending)
    return queued


async def watch_audits(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> None:
    """Poll forever. An audit is the slowest job, so it looks the least often."""
    log = (log or create_logger("schedule")).bind(component="watcher")
    while True:
        try:
            await poll_audits(rig, scheduler, log)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("audit cycle failed", reason="watcher_error", error=error)
        await asyncio.sleep(rig.config.schedule.audit_poll_seconds)


async def watch_models(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> None:
    """Keep the window's picture of the model servers current.

    It starts nothing. A run starts the backend it needs and waits for it, so a server
    that is down costs the next run a minute, not a failure. Starting one here as well
    would undo Unload within a poll and take the memory straight back.
    """
    log = (log or create_logger("schedule")).bind(component="watcher")
    while True:
        await asyncio.sleep(rig.config.schedule.model_poll_seconds)
        if getattr(rig, "verifying", False):
            # The sweep stops one model and starts the other. A probe in the middle of
            # that reports a state that is already gone.
            continue
        try:
            health = await rig.check_models()
            down = [
                name
                for name, state in health.items()
                if not state.up and rig.config.backend[name].managed
            ]
            if down:
                log.info("managed models are down", reason="model_down", backends=down)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("model cycle failed", reason="watcher_error", error=error)


async def watch_verify(rig: RigLike, scheduler: Scheduler, log: Logger | None = None) -> None:
    """Have the second model judge what the first one found.

    It waits for the queue to go quiet. Swapping models costs minutes, so doing it
    while reviews are still arriving would spend the whole day loading weights.
    """
    log = (log or create_logger("schedule")).bind(component="watcher")
    while True:
        await asyncio.sleep(rig.config.schedule.verify_poll_seconds)
        if not rig.config.defaults.adversary:
            continue
        if scheduler.pending or scheduler.in_flight:
            continue
        try:
            outcome = await rig.verify_findings()
        except Exception as error:
            log.error("verify sweep failed", reason="verify_failed", error=error)
            continue
        if outcome.judged:
            log.info(
                "verify sweep finished",
                judged=outcome.judged,
                rejected=outcome.rejected,
                kept=outcome.kept,
            )

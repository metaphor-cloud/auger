"""Notice new work.

The watcher compares each repository's HEAD with the commit that was last reviewed. When
they differ, it queues a review of exactly the range between them, so a repository that
gained ten commits overnight is reviewed once, not ten times.
"""

from __future__ import annotations

import asyncio

from reviewrig.log import Logger, create_logger
from reviewrig.schedule.protocol import RigLike
from reviewrig.schedule.scheduler import Scheduler, Task
from reviewrig.store.runs import reviewed_head
from reviewrig.watch import git


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

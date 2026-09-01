"""Where the work has got to, while it is still running.

A run publishes when it starts and when it finishes, and in between it can spend minutes
inside one step. Without this the window cannot tell work from a hang, so the honest
report is a phase, when the phase started, and how far through it is.

The tracker holds live state only. Nothing here reaches the store or the HTTP layer, and
a job that was given no handle still runs: `nowhere()` answers every call and reports
nothing.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: How often progress inside one phase is published. A phase change is always published,
#: because it is the part a person is waiting to read. Progress inside one is not: an
#: embedding loop over a few thousand chunks would otherwise fill every subscriber's
#: queue and push the events that matter out of it.
EVERY = 0.5

#: The phases a job reports. The window turns these into words; the engine only has to
#: agree with itself about the keys.
PHASES = (
    "starting",
    "diff",
    "index",
    "embed",
    "retrieve",
    "rerank",
    "scan",
    "outline",
    "reading",
    "asking",
    "tool",
    "parsing",
    "repairing",
    "verifying",
    "saving",
    "posting",
)

_ids = itertools.count(1)


@dataclass
class Step:
    """One run in flight."""

    id: int
    repo: str
    slug: str
    kind: str
    #: Wall clock, because the window subtracts it from the reader's own clock.
    started: float
    phase: str = "starting"
    phase_started: float = 0.0
    detail: str = ""
    #: Progress inside a countable phase. `total` of 0 means the phase cannot be counted,
    #: which is the honest answer for most of them.
    done: int = 0
    total: int = 0
    #: Answer tokens seen so far, while a model is streaming one.
    tokens: int = 0
    #: When the current answer began arriving, so a rate can be worked out.
    tokens_started: float = 0.0
    run: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repo": self.repo,
            "slug": self.slug,
            "kind": self.kind,
            "started": self.started,
            "phase": self.phase,
            "phase_started": self.phase_started,
            "detail": self.detail,
            "done": self.done,
            "total": self.total,
            "tokens": self.tokens,
            "tokens_started": self.tokens_started,
            "run": self.run,
        }


Publish = Callable[[str, dict[str, Any]], None]


class Watch:
    """One run's progress, as the jobs see it.

    Every method is safe to call at any time and none of them raise, because a report
    about the work must never be able to stop the work.
    """

    def __init__(
        self,
        step: Step,
        publish: Publish | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.step = step
        self._publish = publish
        self._clock = clock
        self._said = 0.0

    def phase(self, name: str, detail: str = "", total: int = 0) -> None:
        """Move to a new phase. Always published."""
        now = self._clock()
        self.step.phase = name
        self.step.detail = detail
        self.step.phase_started = now
        self.step.done = 0
        self.step.total = total
        self.step.tokens = 0
        self.step.tokens_started = 0.0
        self._say(now, force=True)

    def advance(self, done: int, total: int | None = None, detail: str | None = None) -> None:
        """How far through a countable phase. Published at a bounded rate."""
        self.step.done = done
        if total is not None:
            self.step.total = total
        if detail is not None:
            self.step.detail = detail
        self._say(self._clock())

    def prefill(self, done: int, total: int) -> None:
        """How much of the prompt the server has read.

        A large prompt spends minutes being read before the first token of the answer
        exists. That is the longest silence in the whole rig, and the phase it happens
        in is the same one the answer arrives in, so it is counted rather than named.
        """
        self.step.done = done
        self.step.total = total
        self._say(self._clock())

    def tokens(self, count: int) -> None:
        """How much of an answer has arrived. Published at a bounded rate."""
        now = self._clock()
        if self.step.tokens_started == 0.0:
            self.step.tokens_started = now
        self.step.tokens = count
        self._say(now)

    def names_run(self, run: str) -> None:
        """The run row this step is writing to, so the window can reach it."""
        self.step.run = run
        self._say(self._clock(), force=True)

    def _say(self, now: float, force: bool = False) -> None:
        if self._publish is None:
            return
        if not force and now - self._said < EVERY:
            return
        self._said = now
        self._publish("run.progress", self.step.as_dict())


class Activity:
    """Every run in flight, newest last."""

    def __init__(
        self, publish: Publish | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._publish = publish
        self._clock = clock
        self._live: dict[int, Step] = {}

    def begin(self, repo: str, slug: str, kind: str) -> Watch:
        now = self._clock()
        step = Step(
            id=next(_ids),
            repo=repo,
            slug=slug,
            kind=kind,
            started=now,
            phase_started=now,
        )
        self._live[step.id] = step
        watch = Watch(step, self._publish, self._clock)
        watch._say(now, force=True)
        return watch

    def end(self, watch: Watch) -> None:
        self._live.pop(watch.step.id, None)
        if self._publish is not None:
            self._publish("run.progress", {**watch.step.as_dict(), "phase": "done"})

    def steps(self) -> list[Step]:
        return sorted(self._live.values(), key=lambda step: step.id)


def nowhere() -> Watch:
    """A handle that reports nothing, for a job called without one."""
    return Watch(Step(id=0, repo="", slug="", kind="", started=0.0))

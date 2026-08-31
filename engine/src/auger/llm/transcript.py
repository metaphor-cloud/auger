"""What the rig said to the model, and what the model said back.

A review that runs all day is invisible. The transcript is the window into it: every
exchange, in order, while it happens.

It is held in memory and never written to disk. It carries the code under review, so it
lives exactly as long as the process does, and a restart starts it empty.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field

#: How many exchanges are kept. A review sends one prompt and receives one answer, so
#: this is a few hours of work at a normal pace.
DEPTH = 120
#: How much of one message is kept. A whole repository audit would otherwise hold the
#: repository in memory twice over.
MAX_CHARS = 24_000


@dataclass(frozen=True)
class Turn:
    """One exchange with one model."""

    id: int
    at: float
    backend: str
    model: str
    job_class: str
    #: The repository the work belongs to, when the caller knows it.
    repo: str
    prompt: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    error: str | None = None
    #: What the model asked to run, when it asked for a tool instead of answering.
    #: A tool call carries no text, so without this the turn reads as silence.
    tools: tuple[str, ...] = ()

    @property
    def clipped(self) -> bool:
        return len(self.prompt) >= MAX_CHARS or len(self.answer) >= MAX_CHARS


def clip(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return f"{text[:MAX_CHARS]}\n[{len(text) - MAX_CHARS} more characters]"


@dataclass
class Transcript:
    """The last few exchanges, newest last."""

    turns: deque[Turn] = field(default_factory=lambda: deque(maxlen=DEPTH))
    _next: int = 1

    def add(
        self,
        backend: str,
        model: str,
        job_class: str,
        prompt: str,
        answer: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int = 0,
        repo: str = "",
        error: str | None = None,
        tools: tuple[str, ...] = (),
    ) -> Turn:
        turn = Turn(
            id=self._next,
            at=time.time(),
            backend=backend,
            model=model,
            job_class=job_class,
            repo=repo,
            prompt=clip(prompt),
            answer=clip(answer),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
            error=error,
            tools=tools,
        )
        self._next += 1
        self.turns.append(turn)
        return turn

    def since(self, after: int = 0, limit: int = DEPTH) -> list[Turn]:
        found = [turn for turn in self.turns if turn.id > after]
        return found[-limit:]

    def clear(self) -> None:
        self.turns.clear()

    def __len__(self) -> int:
        return len(self.turns)

    def __iter__(self) -> Iterator[Turn]:
        return iter(self.turns)

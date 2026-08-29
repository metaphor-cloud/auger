"""How long since anybody touched this machine.

A review holds two cores and tens of gigabytes. On a laptop that is the difference
between a quiet machine and a hot one, so the rig can be told to wait until its owner
has stopped working.

A platform that cannot answer never blocks the work. Refusing to review because the
question could not be asked would be worse than reviewing at a bad moment.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass

#: `ioreg` reports the idle time in nanoseconds, on a line of its own.
_HID_IDLE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
#: Without the properties there is no idle time to read, so the depth is not limited.
COMMAND = ("ioreg", "-c", "IOHIDSystem")
TIMEOUT = 3.0
#: How long an answer is trusted. The gate is checked per task, and a review runs for
#: minutes, so asking the operating system every time buys nothing.
CACHE_SECONDS = 5.0


@dataclass
class Idle:
    """How long the machine has been left alone."""

    seconds: float
    #: False when the platform cannot say. The caller treats that as free.
    known: bool = True

    def free_for(self, wanted: int) -> bool:
        return not self.known or self.seconds >= wanted


_last: tuple[float, Idle] | None = None


def measure() -> Idle:
    """Ask the operating system how long the input devices have been quiet."""
    if sys.platform != "darwin":
        return Idle(seconds=0.0, known=False)
    try:
        output = subprocess.run(
            COMMAND, capture_output=True, text=True, timeout=TIMEOUT, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return Idle(seconds=0.0, known=False)
    found = _HID_IDLE.search(output)
    if found is None:
        return Idle(seconds=0.0, known=False)
    return Idle(seconds=int(found.group(1)) / 1_000_000_000)


def current(now: float | None = None) -> Idle:
    """The last answer, or a fresh one when it has gone stale."""
    global _last
    stamp = now if now is not None else time.monotonic()
    if _last is not None and stamp - _last[0] < CACHE_SECONDS:
        return _last[1]
    found = measure()
    _last = (stamp, found)
    return found


def forget() -> None:
    """Drop the cached answer. For a test, and for a setting that just changed."""
    global _last
    _last = None

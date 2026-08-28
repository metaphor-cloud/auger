"""Stop the engine when its parent process disappears.

Without this watch, an engine orphan would keep a port, a database, and a model server
busy after the application that started it is gone.

The engine watches its parent process id, not its stdin. A closed stdin pipe looks like
the obvious signal, but the pipe stayed open in a real test after the host took a
SIGKILL, so the engine never saw the end of file. The parent process id is unambiguous:
when the parent dies, the kernel reparents this process and the id changes.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

INTERVAL_SECONDS = 2.0


def watch_for_reparent(
    read_ppid: Callable[[], int],
    on_reparent: Callable[[], None],
    interval: float = INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until the parent process id changes, then call `on_reparent`."""
    original = read_ppid()
    while read_ppid() == original:
        sleep(interval)
    on_reparent()


def watch_parent(
    on_reparent: Callable[[], None], interval: float = INTERVAL_SECONDS
) -> threading.Thread | None:
    """Start the watch in a daemon thread. Returns None if there is no parent to watch."""
    if os.getppid() <= 1:
        return None
    thread = threading.Thread(
        target=watch_for_reparent,
        args=(os.getppid, on_reparent, interval),
        name="parent-watch",
        daemon=True,
    )
    thread.start()
    return thread

"""Start a managed model server, once."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class Server:
    name: str
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> bool:
        """True when this call started it. Never starts one twice."""
        with self.lock:
            if self.running:
                return False
            self.running = self._spawn()
            return self.running

    def _spawn(self) -> bool:
        return True

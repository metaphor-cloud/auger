"""Pick a backend.

Order: Apple `container`, then Podman, then Docker, then Seatbelt. Seatbelt is a real
loss of isolation, so the choice carries a warning that the UI shows until the user
installs a container runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from auger.log import Logger, create_logger
from auger.sandbox.base import Sandbox
from auger.sandbox.oci import AppleContainer, Docker, Podman
from auger.sandbox.seatbelt import Seatbelt

SEATBELT_WARNING = (
    "No container runtime found, so analysis runs under Seatbelt on the host. "
    "It has no network and cannot write to your repository, but it shares the host "
    "kernel, user, and file system. Install Apple `container`, Podman, or Docker for "
    "full isolation."
)


@dataclass(frozen=True)
class Selection:
    sandbox: Sandbox
    warning: str | None = None

    @property
    def degraded(self) -> bool:
        return self.warning is not None


def backends(log: Logger | None = None) -> list[Sandbox]:
    log = log or create_logger("sandbox")
    return [AppleContainer(log), Podman(log), Docker(log), Seatbelt(log)]


def select(log: Logger | None = None) -> Selection:
    """Return the first available backend. Raises when even Seatbelt is missing."""
    log = log or create_logger("sandbox")
    for sandbox in backends(log):
        if not sandbox.available():
            continue
        if sandbox.name == Seatbelt.name:
            log.warn("sandbox degraded", reason="no_container_runtime", backend=sandbox.name)
            return Selection(sandbox, SEATBELT_WARNING)
        log.info("sandbox selected", backend=sandbox.name)
        return Selection(sandbox)
    raise RuntimeError("no sandbox backend is available on this machine")

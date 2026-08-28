"""The sandbox contract.

Every analysis step runs through one of these. The rules are the same for every backend:

- The repository is mounted read only. Analysis never writes to the user's code.
- Scratch space is a size limited tmpfs that disappears with the run.
- The process runs as a user with no privileges and no capabilities.
- There is no network. See `Network` for why the default is absolute.
- A run that passes its time limit is killed.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

#: Where the repository appears inside the sandbox.
WORK = "/work"
#: Writable, in memory, and gone when the run ends.
SCRATCH = "/scratch"
#: `nobody` on every common base image.
NOBODY = "65534:65534"


class Network(StrEnum):
    """How much of the network a run may reach.

    `NONE` is the default and covers every review step. The model call happens in the
    engine on the host, not in the sandbox, because a container on macOS cannot reach
    Metal and because a container with no network cannot leak anything at all.

    `NAT` exists for the one job that cannot work without it: a step that installs
    dependencies before it builds untrusted code. It gives full outbound access, so the
    UI names every repository that uses it.
    """

    NONE = "none"
    NAT = "nat"


class SandboxError(RuntimeError):
    """The backend could not start the run."""


@dataclass(frozen=True)
class RunSpec:
    repository: Path
    command: Sequence[str]
    image: str
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 300.0
    network: Network = Network.NONE
    scratch_mb: int = 512
    memory_mb: int = 2048
    cpus: int = 2
    workdir: str = WORK
    user: str = NOBODY

    def shell_command(self) -> str:
        return shlex.join(self.command)


@dataclass(frozen=True)
class RunResult:
    backend: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@runtime_checkable
class Sandbox(Protocol):
    name: str

    def available(self) -> bool:
        """True when this backend can run on this machine right now."""

    def run(self, spec: RunSpec) -> RunResult: ...

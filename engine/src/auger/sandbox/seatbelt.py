"""The fallback backend.

Seatbelt confines a process on the host. It is weaker than a container: the process sees
the whole file system, and it shares the host kernel and the host user. The rig uses it
only when no container runtime exists, and the UI says so, because the user has to know
which isolation they lost.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from auger.log import Logger, create_logger
from auger.sandbox.base import ImageState, Network, RunResult, RunSpec, SandboxError
from auger.sandbox.isolation import assert_no_credentials

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

PROFILE = """(version 1)
(allow default)

; No network. The model call happens in the engine, never in a sandboxed step.
(deny network*)

; The repository is the user's code. A review reads it and never writes to it.
(deny file-write*)
(allow file-write*
    (subpath "{scratch}")
    (literal "/dev/null")
    (literal "/dev/zero")
    (literal "/dev/random")
    (literal "/dev/urandom")
    (literal "/dev/dtracehelper")
    (literal "/dev/tty"))
"""


class Seatbelt:
    """Run the command under `sandbox-exec` with a deny-write, deny-network profile."""

    name = "seatbelt"

    def __init__(self, log: Logger | None = None) -> None:
        self.log = (log or create_logger("sandbox")).bind(backend=self.name)
        # Seatbelt runs the command on the host with the host's own tools, so no
        # image exists to fetch and none can be missing.
        self.image_state = ImageState.UNUSED
        self.image_error: str | None = None
        self.on_image_state: Callable[[ImageState, str | None], None] | None = None

    def available(self) -> bool:
        return Path(SANDBOX_EXEC).exists()

    def ensure_image(self, reference: str) -> bool:
        """Always ready. There is no image to get."""
        return True

    def profile(self, scratch: Path) -> str:
        return PROFILE.format(scratch=scratch)

    def run(self, spec: RunSpec) -> RunResult:
        if spec.network is not Network.NONE:
            raise SandboxError("seatbelt runs offline only. Install a container runtime.")
        if not spec.repository.is_dir():
            raise SandboxError(f"repository not found: {spec.repository}")
        assert_no_credentials(spec.env)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="auger-scratch-") as scratch:
            scratch_path = Path(scratch).resolve()
            profile = self.profile(scratch_path)
            environment = {
                **spec.env,
                "AUGER_WORK": str(spec.repository),
                "AUGER_SCRATCH": str(scratch_path),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
                "HOME": str(scratch_path),
                "TMPDIR": str(scratch_path),
            }
            arguments = [SANDBOX_EXEC, "-p", profile, *spec.command]
            self.log.debug("sandbox run", command=spec.shell_command())
            try:
                completed = subprocess.run(
                    arguments,
                    capture_output=True,
                    text=True,
                    timeout=spec.timeout_seconds,
                    check=False,
                    cwd=spec.repository,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                self.log.warn(
                    "sandbox run timed out",
                    reason="timeout",
                    seconds=spec.timeout_seconds,
                    command=spec.shell_command(),
                )
                return RunResult(
                    backend=self.name,
                    exit_code=124,
                    stdout="",
                    stderr="",
                    duration_seconds=time.monotonic() - started,
                    timed_out=True,
                )
            except OSError as error:
                raise SandboxError(f"sandbox-exec could not start: {error}") from error
        return RunResult(
            backend=self.name,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - started,
        )

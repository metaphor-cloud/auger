"""Container backends.

Apple `container`, Podman, and Docker take almost the same arguments. The differences
live in `extra_arguments`, and the shared code builds everything else, so one set of
tests covers the rules that matter.
"""

from __future__ import annotations

import subprocess
import time
import uuid

from reviewrig.log import Logger, create_logger
from reviewrig.sandbox.base import SCRATCH, WORK, Network, RunResult, RunSpec, SandboxError
from reviewrig.sandbox.which import find


class OciSandbox:
    """A container per run, removed when the run ends."""

    name = "oci"
    executable = ""

    def __init__(self, log: Logger | None = None) -> None:
        self.log = (log or create_logger("sandbox")).bind(backend=self.name)

    def available(self) -> bool:
        return self.program() is not None

    def program(self) -> str | None:
        """The full path, because a graphical application has a narrow PATH."""
        return find(self.executable)

    def extra_arguments(self, spec: RunSpec) -> list[str]:
        """Flags that only this runtime understands."""
        return []

    def tmpfs_argument(self, spec: RunSpec) -> str:
        """Apple `container` takes a bare path. Podman and Docker take options too."""
        return SCRATCH

    def arguments(self, spec: RunSpec, container_name: str) -> list[str]:
        """Build the whole command line. Kept pure, so a test can read every rule."""
        arguments = [
            self.program() or self.executable,
            "run",
            "--rm",
            "--name",
            container_name,
            # The repository is the user's code. A review never writes to it.
            "--volume",
            f"{spec.repository}:{WORK}:ro",
            # Scratch is in memory and disappears with the run.
            "--tmpfs",
            self.tmpfs_argument(spec),
            "--workdir",
            spec.workdir,
            "--user",
            spec.user,
            "--cap-drop",
            "ALL",
            "--memory",
            f"{spec.memory_mb}M",
            "--cpus",
            str(spec.cpus),
        ]
        if spec.network is Network.NONE:
            arguments += ["--network", "none"]
        arguments += self.extra_arguments(spec)
        for key, value in sorted(spec.env.items()):
            arguments += ["--env", f"{key}={value}"]
        arguments.append(spec.image)
        arguments += list(spec.command)
        return arguments

    def _kill(self, container_name: str) -> None:
        subprocess.run(
            [self.executable, "kill", container_name],
            capture_output=True,
            check=False,
            timeout=30,
        )

    def run(self, spec: RunSpec) -> RunResult:
        if not spec.repository.is_dir():
            raise SandboxError(f"repository not found: {spec.repository}")
        container_name = f"reviewrig-{uuid.uuid4().hex[:12]}"
        arguments = self.arguments(spec, container_name)
        log = self.log.bind(container=container_name)
        log.debug("sandbox run", command=spec.shell_command(), network=str(spec.network))
        started = time.monotonic()
        try:
            completed = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            # The client gave up, so the container is still running. Stop it, or it
            # holds memory and a repository mount until the machine restarts.
            self._kill(container_name)
            log.warn(
                "sandbox run timed out",
                reason="timeout",
                seconds=spec.timeout_seconds,
                command=spec.shell_command(),
            )
            return RunResult(
                backend=self.name,
                exit_code=124,
                stdout=_text(expired.stdout),
                stderr=_text(expired.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )
        except OSError as error:
            raise SandboxError(f"{self.executable} could not start: {error}") from error
        return RunResult(
            backend=self.name,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - started,
        )


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


class AppleContainer(OciSandbox):
    """Apple's `container`, one light virtual machine per container. macOS 26 and later."""

    name = "apple-container"
    executable = "container"

    def extra_arguments(self, spec: RunSpec) -> list[str]:
        # No name resolution, so a leaked host name cannot become a lookup.
        return ["--no-dns"] if spec.network is Network.NONE else []


class Podman(OciSandbox):
    name = "podman"
    executable = "podman"

    def extra_arguments(self, spec: RunSpec) -> list[str]:
        return ["--security-opt", "no-new-privileges"]

    def tmpfs_argument(self, spec: RunSpec) -> str:
        return f"{SCRATCH}:rw,size={spec.scratch_mb}m,noexec,nosuid"


class Docker(Podman):
    name = "docker"
    executable = "docker"

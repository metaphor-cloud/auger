"""Container backends.

Apple `container`, Podman, and Docker take almost the same arguments. The differences
live in `extra_arguments`, and the shared code builds everything else, so one set of
tests covers the rules that matter.
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Callable

from auger.log import Logger, create_logger
from auger.sandbox.base import (
    SCRATCH,
    WORK,
    ImageState,
    Network,
    RunResult,
    RunSpec,
    SandboxError,
)
from auger.sandbox.isolation import assert_contained, assert_no_credentials
from auger.sandbox.which import find


class OciSandbox:
    """A container per run, removed when the run ends."""

    name = "oci"
    executable = ""

    #: A first download is most of a gigabyte over whatever line the user has.
    PULL_TIMEOUT_SECONDS = 1800.0

    def __init__(self, log: Logger | None = None) -> None:
        self.log = (log or create_logger("sandbox")).bind(backend=self.name)
        self.image_state = ImageState.UNKNOWN
        self.image_error: str | None = None
        self.on_image_state: Callable[[ImageState, str | None], None] | None = None
        # Runs happen on worker threads, and the first two would otherwise start the
        # same download twice. The second waits here and then finds the image present.
        self._image_lock = threading.Lock()

    def available(self) -> bool:
        return self.program() is not None

    def _set_image_state(self, state: ImageState, error: str | None = None) -> None:
        self.image_state = state
        self.image_error = error
        if self.on_image_state is not None:
            self.on_image_state(state, error)

    def has_image(self, reference: str) -> bool:
        """Whether the image is already on this machine. Never raises."""
        try:
            completed = subprocess.run(
                [self.program() or self.executable, "image", "inspect", reference],
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def ensure_image(self, reference: str) -> bool:
        with self._image_lock:
            if self.has_image(reference):
                self._set_image_state(ImageState.PRESENT)
                return True
            self._set_image_state(ImageState.PULLING)
            self.log.info("image pull started", image=reference)
            try:
                completed = subprocess.run(
                    [self.program() or self.executable, "image", "pull", reference],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.PULL_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                reason = f"the download passed {self.PULL_TIMEOUT_SECONDS:.0f} seconds"
                self.log.error("image pull timed out", reason="timeout", image=reference)
                self._set_image_state(ImageState.FAILED, reason)
                return False
            except OSError as error:
                self.log.error("image pull could not start", reason="exec_failed", error=str(error))
                self._set_image_state(ImageState.FAILED, str(error))
                return False
            if completed.returncode != 0:
                # The runtime's own last line says more than any sentence here would:
                # a 401 on a private package, a name that does not resolve, no disk.
                reason = _last_line(completed.stderr) or _last_line(completed.stdout)
                self.log.error(
                    "image pull failed",
                    reason="pull_failed",
                    image=reference,
                    exit_code=completed.returncode,
                    detail=reason,
                )
                self._set_image_state(ImageState.FAILED, reason or "the download failed")
                return False
            self.log.info("image pull finished", image=reference)
            self._set_image_state(ImageState.PRESENT)
            return True

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
        # The download at start-up may have failed, or the user may have deleted the
        # image since. Either way this is the last chance to get it, and a run that
        # starts without it fails with a message about the runtime instead.
        if not self.ensure_image(spec.image):
            raise SandboxError(f"the analysis image is missing: {self.image_error}")
        container_name = f"auger-{uuid.uuid4().hex[:12]}"
        arguments = self.arguments(spec, container_name)
        # Audited here rather than where the line is built, so no path to a container
        # skips it: a future caller that adds a flag of its own is checked too.
        assert_no_credentials(spec.env)
        assert_contained(arguments, [spec.repository])
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


def _last_line(text: str) -> str:
    """The runtime's own last word, which is the part that names the failure."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


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

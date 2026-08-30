"""A command tool, pointed at the sandbox.

A reviewer that can only read what it was handed has to take the code's word for what it
does. One that can run a command can check. This gives it that, and gives it nothing
else: the command goes to `sandbox.run`, which is the only way anything derived from a
repository is ever executed.

The model is also told what it is working inside. A tool whose limits are a surprise
gets used badly - a model that does not know each command starts a fresh container will
spend its budget installing something and then wondering where it went. So the
description is generated from the same values the run actually uses, and cannot drift
away from them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auger.log import Logger, create_logger
from auger.sandbox import WORK, Network, RunSpec, Sandbox, SandboxError, Seatbelt

NAME = "run_command"

DESCRIPTION = (
    "Run one shell command inside the analysis sandbox and return what it printed. "
    "Use it to check what the code actually does: run the tests, execute a snippet, "
    "inspect a file, ask a tool. Read the sandbox notes in your instructions first, "
    "because they say what is and is not possible in there."
)


@dataclass(frozen=True)
class Shell:
    """The sandbox, as something a model can call."""

    sandbox: Sandbox
    repository: Path
    image: str
    timeout_seconds: float = 120.0
    #: Output past this is cut. A command that prints a whole build log would otherwise
    #: spend the context the review needs to reach an answer.
    output_limit: int = 6000

    @property
    def degraded(self) -> bool:
        return self.sandbox.name == Seatbelt.name

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": NAME,
                "description": DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command, as you would type it in a shell.",
                        }
                    },
                    "required": ["command"],
                },
            },
        }

    def notes(self) -> str:
        """What the model is told about the room it is in.

        Every number here is read from the values a run is given, so the description
        and the sandbox cannot disagree.
        """
        where = (
            "on this machine under a Seatbelt profile, which is weaker than a container"
            if self.degraded
            else "in a container"
        )
        return f"""

You have a `{NAME}` tool. It runs {where}, under these conditions:

- The repository is at `{WORK}` and is read only. You cannot change the code.
- `/scratch` is writable, held in memory, and emptied when the command ends.
- There is no network. Nothing can be fetched, installed from a registry, or reached.
- The command runs as a user with no privileges and no capabilities.
- Each call starts fresh. Nothing survives between calls: not the working directory, \
not a file you wrote outside `{WORK}`, not anything you installed. Write one command \
that does the whole job, joining steps with `&&`, rather than a sequence of calls.
- A command has {self.timeout_seconds:.0f} seconds. After that it is killed.
- Output longer than {self.output_limit} characters is cut. Ask for what you need: \
pipe through `head`, `grep`, or `wc`.
- The image is a small analysis image and the project's own toolchain is probably not \
installed. Find out by running something rather than assuming either way.

Use it when running something would settle a question you would otherwise have to \
guess at. If a command fails because the sandbox cannot do that, do not keep trying: \
say what you could not check and review on what you can see.
"""

    async def run(self, command: str, log: Logger | None = None) -> str:
        log = (log or create_logger("jobs")).bind(component="shell")
        spec = RunSpec(
            repository=self.repository,
            command=["sh", "-c", command],
            image=self.image,
            timeout_seconds=self.timeout_seconds,
            network=Network.NONE,
        )
        try:
            result = await asyncio.to_thread(self.sandbox.run, spec)
        except SandboxError as error:
            log.warn("command could not start", reason="sandbox_failed", error=error)
            return f"The sandbox could not run that: {error}"
        if result.timed_out:
            log.warn("command timed out", reason="timeout", seconds=self.timeout_seconds)
            return f"Killed after {self.timeout_seconds:.0f} seconds. Nothing was returned."
        log.debug("command ran", exit_code=result.exit_code, seconds=result.duration_seconds)
        return self._report(result.exit_code, result.stdout, result.stderr)

    def _report(self, exit_code: int, stdout: str, stderr: str) -> str:
        parts = [f"exit code {exit_code}"]
        for label, stream in (("stdout", stdout), ("stderr", stderr)):
            text = stream.strip()
            if not text:
                continue
            if len(text) > self.output_limit:
                text = text[: self.output_limit] + f"\n[cut after {self.output_limit} characters]"
            parts.append(f"{label}:\n{text}")
        if len(parts) == 1:
            parts.append("It printed nothing.")
        return "\n\n".join(parts)

"""A repository's compose file is a request, not a configuration.

The compose schema can ask for the host in plain words: bind the root filesystem, take
the host network, run privileged, build an image by executing a Dockerfile. Handing a
repository's file to the runtime is the whole boundary gone, and it takes no clever
attack to do it. The file simply says so.

So the runtime never sees the repository's file. This reads it, keeps the keys on an
allowlist, holds every bind mount inside the project, and returns a document auger
wrote. Everything dropped is reported, because a service that will not come up should
say why rather than fail obscurely.

An allowlist is right here and a list of forbidden words was not right for commands.
The difference is that the compose schema is closed and published, so what is left over
after an allowlist is a known quantity. The set of ways to spell a shell command is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Top level keys worth keeping. `secrets` and `configs` both name files on this
#: machine, and `version` has been meaningless since the compose spec absorbed it.
TOP_LEVEL = frozenset({"services", "networks", "volumes", "name"})

#: Service keys worth keeping.
#:
#: `build` is absent on purpose: it runs a Dockerfile, which is arbitrary code with
#: network access at build time. A service that only builds cannot run here.
#:
#: `ports` is absent because it publishes on the user's machine. Services are reached
#: over the private network, so nothing needs a host port, and binding one would put a
#: repository's server on a developer's laptop as a side effect of a review.
SERVICE = frozenset(
    {
        "image",
        "command",
        "entrypoint",
        "environment",
        "expose",
        "depends_on",
        "healthcheck",
        "working_dir",
        "user",
        "labels",
        "networks",
        "volumes",
        "tmpfs",
        "init",
        "read_only",
        "stop_signal",
        "stop_grace_period",
        "cap_drop",
    }
)


@dataclass(frozen=True)
class Sanitised:
    """What survived, and what did not."""

    document: dict[str, Any]
    dropped: list[str] = field(default_factory=list)

    @property
    def services(self) -> dict[str, Any]:
        services = self.document.get("services", {})
        return services if isinstance(services, dict) else {}


class ComposeError(ValueError):
    """The file is not a compose document this can work with."""


def load(text: str) -> dict[str, Any]:
    """Parse without executing.

    `safe_load` and never `load`: the unsafe loader constructs arbitrary Python objects
    named in the document, which would make reading the file the very thing this module
    exists to prevent.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ComposeError(f"could not read the compose file: {error}") from error
    if not isinstance(document, dict):
        raise ComposeError("a compose file has to be a mapping")
    return document


def _escape(value: Any) -> Any:
    """Neutralise interpolation, everywhere, at every depth.

    Compose expands `${VAR}` from the environment of whichever process runs it, and that
    process is the engine on the host. A repository could therefore read the host's
    environment by writing `${AWS_SECRET_ACCESS_KEY}` into a label. Doubling the dollar
    is the schema's own escape, so the text survives and the expansion does not.
    """
    if isinstance(value, str):
        return value.replace("$", "$$") if "$" in value else value
    if isinstance(value, dict):
        return {key: _escape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_escape(item) for item in value]
    return value


def _bind_source(entry: Any) -> str | None:
    """The host side of a volume entry, or None when it names no path."""
    if isinstance(entry, dict):
        if entry.get("type", "bind") != "bind":
            return None
        source = entry.get("source")
        return source if isinstance(source, str) else None
    if not isinstance(entry, str):
        return None
    source = entry.split(":", 1)[0]
    return source if source.startswith((".", "/", "~")) else None


def _within(source: str, root: Path) -> bool:
    if source.startswith("~"):
        return False
    candidate = (root / source) if not source.startswith("/") else Path(source)
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def _volumes(entries: Any, root: Path, service: str, dropped: list[str]) -> list[Any]:
    kept: list[Any] = []
    for entry in entries if isinstance(entries, list) else []:
        source = _bind_source(entry)
        # No source means a named or anonymous volume, which reaches nothing on the host.
        if source is None or _within(source, root):
            kept.append(entry)
        else:
            dropped.append(f"{service}: volume {source} is outside the project")
    return kept


def sanitise(document: dict[str, Any], root: Path) -> Sanitised:
    """Rewrite a parsed compose document into one that can only reach the project.

    `root` is the directory the services may see, which is the disposable clone and
    never the user's own tree.
    """
    root = root.expanduser().resolve()
    dropped: list[str] = []
    clean: dict[str, Any] = {}

    for key, value in document.items():
        if key in TOP_LEVEL:
            clean[key] = value
        elif not key.startswith("x-"):
            dropped.append(f"top level: {key}")

    services = clean.get("services")
    if not isinstance(services, dict):
        raise ComposeError("the compose file declares no services")

    kept_services: dict[str, Any] = {}
    for name, definition in services.items():
        if not isinstance(definition, dict):
            dropped.append(f"{name}: not a mapping")
            continue
        service: dict[str, Any] = {}
        for key, value in definition.items():
            if key not in SERVICE:
                dropped.append(f"{name}: {key}")
                continue
            service[key] = _volumes(value, root, name, dropped) if key == "volumes" else value
        if "image" not in service:
            # Whatever it needed came from `build`, which cannot run here.
            dropped.append(f"{name}: no image to run without building one")
            continue
        kept_services[name] = service

    if not kept_services:
        raise ComposeError("nothing in the compose file can run under these rules")

    clean["services"] = kept_services
    return Sanitised(document=_escape(clean), dropped=dropped)


def write(sanitised: Sanitised, path: Path) -> Path:
    """Write the document auger produced. The runtime is only ever given this one."""
    path.write_text(yaml.safe_dump(sanitised.document, sort_keys=True), encoding="utf-8")
    return path

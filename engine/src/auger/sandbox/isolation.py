"""What a sandboxed run may not ask for.

The sandbox rules are only worth as much as the code that cannot break them. Every run
is audited against these before it starts, so a flag that would hand over the host is an
exception rather than a review comment somebody has to remember to make.

Two things are checked. The command line may not carry a flag that reaches past the
container, and the environment may not carry anything that could be a credential.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from auger.sandbox.base import SandboxError

#: Flags that end the isolation, whatever else the line says. Each one either gives the
#: process the host's privileges, its namespaces, or its devices.
#:
#: `--security-opt` is here for its unconfined forms only, because the backends pass
#: `no-new-privileges` through it, which is the opposite of an escape.
FORBIDDEN = (
    "--privileged",
    "--cap-add",
    "--device",
    "--device-cgroup-rule",
    "--volumes-from",
    "--userns",
    "--cgroupns",
)

#: Namespace flags that are safe unless they name the host.
NAMESPACE = ("--pid", "--ipc", "--uts", "--network")

#: What `--security-opt` may say. Anything else is refused rather than guessed at.
SECURITY_OPTIONS = ("no-new-privileges",)

#: Flags whose value names a path on this machine.
MOUNTS = ("--volume", "-v", "--mount")

#: Environment variable names a run may be given.
#:
#: This is an allowlist and not a list of forbidden names, because the set of ways to
#: spell a secret is open and the set of variables a build legitimately needs is small.
#: Nothing here can carry a credential. Adding a name is a deliberate change that shows
#: up in a diff, which is the point.
ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TERM",
        "LANG",
        "LC_ALL",
        "TZ",
        "CI",
        "NODE_ENV",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "AUGER_WORK",
        "AUGER_SCRATCH",
    }
)


def _split(argument: str) -> tuple[str, str | None]:
    """`--flag=value` and `--flag` alike, as a name and whatever followed the equals."""
    name, separator, value = argument.partition("=")
    return name, value if separator else None


def _host_path(mount: str) -> str | None:
    """The host side of a mount, or None when it names no path on this machine.

    Short form is `host:container[:options]`. Long form is a comma separated list of
    `key=value`, where a bind names its host side with `source` or `src`.
    """
    if "=" in mount and "," in mount:
        fields = dict(field.split("=", 1) for field in mount.split(",") if "=" in field)
        if fields.get("type", "bind") != "bind":
            return None
        return fields.get("source") or fields.get("src")
    host = mount.split(":", 1)[0]
    # A named volume is not a path, and neither is an anonymous one.
    return host if host.startswith(("/", "./", "../", "~")) else None


def _inside(path: str, roots: Sequence[Path]) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def assert_contained(arguments: Sequence[str], roots: Sequence[Path]) -> None:
    """Refuse a command line that reaches past the container.

    `roots` are the only places on this machine a run may mount. Everything else on the
    host is out of reach, including the runtime's own socket, which is what turns a
    container into the machine it runs on.
    """
    resolved = [root.expanduser().resolve() for root in roots]
    index = 0
    while index < len(arguments):
        name, inline = _split(arguments[index])
        index += 1
        if name in FORBIDDEN:
            raise SandboxError(f"{name} would end the isolation and is never allowed")
        takes_value = inline is None and index < len(arguments)
        if name in NAMESPACE:
            value = inline if inline is not None else (arguments[index] if takes_value else "")
            if takes_value:
                index += 1
            if value == "host":
                raise SandboxError(f"{name} host would end the isolation")
        elif name == "--security-opt":
            value = inline if inline is not None else (arguments[index] if takes_value else "")
            if takes_value:
                index += 1
            if value not in SECURITY_OPTIONS:
                raise SandboxError(f"unrecognised security option: {value}")
        elif name in MOUNTS:
            value = inline if inline is not None else (arguments[index] if takes_value else "")
            if takes_value:
                index += 1
            host = _host_path(value)
            if host is not None and not _inside(host, resolved):
                raise SandboxError(f"a run may not mount {host}")


def assert_no_credentials(environment: Mapping[str, str]) -> None:
    """Refuse an environment that carries a name outside the allowlist.

    A sandboxed run is given the code and nothing else. A token in the environment would
    survive into whatever the repository asked to run, which is the one thing a
    container with no network still could not protect.
    """
    unknown = sorted(set(environment) - ENVIRONMENT)
    if unknown:
        raise SandboxError(
            "a run may not be given " + ", ".join(unknown) + ": add the name to "
            "auger.sandbox.isolation.ENVIRONMENT if it really carries no secret"
        )

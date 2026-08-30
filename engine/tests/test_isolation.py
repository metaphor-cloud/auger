"""The boundary, checked as rules rather than as intentions.

Every case here is something that would hand over the host if it reached a runtime. The
audit is pure, so none of this needs a container.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auger.sandbox import (
    AppleContainer,
    Docker,
    Network,
    Podman,
    RunSpec,
    SandboxError,
    assert_contained,
    assert_no_credentials,
)
from auger.sandbox.isolation import ENVIRONMENT
from auger.sandbox.oci import OciSandbox

IMAGE = "python:3.12-alpine"


def spec(tmp_path: Path, **kwargs: object) -> RunSpec:
    return RunSpec(repository=tmp_path, command=["true"], image=IMAGE, **kwargs)  # type: ignore[arg-type]


# --- flags that end the isolation ------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        ["run", "--privileged", IMAGE],
        ["run", "--cap-add", "SYS_ADMIN", IMAGE],
        ["run", "--cap-add=SYS_ADMIN", IMAGE],
        ["run", "--device", "/dev/kmem", IMAGE],
        ["run", "--volumes-from", "other", IMAGE],
        ["run", "--userns", "host", IMAGE],
        ["run", "--cgroupns", "host", IMAGE],
    ],
)
def test_a_flag_that_reaches_the_host_is_refused(line: list[str], tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        assert_contained(line, [tmp_path])


@pytest.mark.parametrize("namespace", ["--pid", "--ipc", "--uts", "--network"])
def test_a_namespace_may_not_be_the_hosts(namespace: str, tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        assert_contained(["run", namespace, "host", IMAGE], [tmp_path])
    with pytest.raises(SandboxError):
        assert_contained(["run", f"{namespace}=host", IMAGE], [tmp_path])


@pytest.mark.parametrize("namespace", ["--pid", "--ipc", "--uts", "--network"])
def test_a_namespace_of_its_own_is_fine(namespace: str, tmp_path: Path) -> None:
    assert_contained(["run", namespace, "none", IMAGE], [tmp_path])


def test_only_known_security_options_pass(tmp_path: Path) -> None:
    assert_contained(["run", "--security-opt", "no-new-privileges", IMAGE], [tmp_path])
    with pytest.raises(SandboxError):
        assert_contained(["run", "--security-opt", "seccomp=unconfined", IMAGE], [tmp_path])
    with pytest.raises(SandboxError):
        assert_contained(["run", "--security-opt", "label=disable", IMAGE], [tmp_path])


# --- mounts ----------------------------------------------------------------------------


def test_a_mount_inside_the_root_is_allowed(tmp_path: Path) -> None:
    inner = tmp_path / "package"
    inner.mkdir()
    assert_contained(["run", "--volume", f"{inner}:/work:ro", IMAGE], [tmp_path])


@pytest.mark.parametrize(
    "mount",
    [
        "/:/host",
        "/etc:/etc:ro",
        "/var/run/docker.sock:/var/run/docker.sock",
        "~/.aws:/aws:ro",
    ],
)
def test_a_mount_outside_the_root_is_refused(mount: str, tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        assert_contained(["run", "--volume", mount, IMAGE], [tmp_path])


def test_the_runtime_socket_is_refused_in_long_form(tmp_path: Path) -> None:
    mount = "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock"
    with pytest.raises(SandboxError):
        assert_contained(["run", "--mount", mount, IMAGE], [tmp_path])


def test_a_named_volume_names_no_host_path(tmp_path: Path) -> None:
    assert_contained(["run", "--volume", "cache:/cache", IMAGE], [tmp_path])
    mount = "type=volume,source=cache,target=/cache"
    assert_contained(["run", "--mount", mount, IMAGE], [tmp_path])


def test_a_symlink_out_of_the_root_does_not_get_in(tmp_path: Path) -> None:
    escape = tmp_path / "escape"
    escape.symlink_to("/etc")
    with pytest.raises(SandboxError):
        assert_contained(["run", "--volume", f"{escape}:/etc:ro", IMAGE], [tmp_path])


# --- every backend passes its own audit ------------------------------------------------


@pytest.mark.parametrize("backend", [AppleContainer(), Podman(), Docker()])
@pytest.mark.parametrize("network", [Network.NONE, Network.NAT])
def test_the_line_a_backend_builds_is_contained(
    backend: OciSandbox, network: Network, tmp_path: Path
) -> None:
    arguments = backend.arguments(spec(tmp_path, network=network), "auger-test")
    assert_contained(arguments, [tmp_path])


# --- the environment -------------------------------------------------------------------


def test_no_environment_is_the_normal_case() -> None:
    assert_no_credentials({})


@pytest.mark.parametrize(
    "name",
    [
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "HF_TOKEN",
        "NPM_TOKEN",
        "OPENAI_API_KEY",
        "SSH_AUTH_SOCK",
    ],
)
def test_a_credential_may_not_be_passed(name: str) -> None:
    with pytest.raises(SandboxError):
        assert_no_credentials({name: "secret"})


def test_an_allowed_name_passes() -> None:
    assert_no_credentials({"PATH": "/usr/bin", "CI": "1"})


def test_the_allowlist_holds_nothing_that_smells_of_a_secret() -> None:
    assert not [
        name
        for name in ENVIRONMENT
        if any(word in name for word in ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH"))
    ]


def test_a_run_with_a_credential_never_starts(tmp_path: Path) -> None:
    from auger.sandbox import Seatbelt

    with pytest.raises(SandboxError):
        Seatbelt().run(spec(tmp_path, env={"GITHUB_TOKEN": "secret"}))

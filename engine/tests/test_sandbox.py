"""Two layers.

The argument builders are pure, so every rule is checked without a container runtime.
The rules that matter, and that a flag alone cannot prove, run against a real container
and skip when no runtime is installed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from auger.sandbox import (
    SEATBELT_WARNING,
    AppleContainer,
    Docker,
    Network,
    OciSandbox,
    Podman,
    RunSpec,
    SandboxError,
    Seatbelt,
    select,
)
from auger.sandbox.select import Selection

IMAGE = "python:3.12-alpine"


def spec(tmp_path: Path, command: list[str], **kwargs: object) -> RunSpec:
    return RunSpec(repository=tmp_path, command=command, image=IMAGE, **kwargs)  # type: ignore[arg-type]


def arguments(backend: OciSandbox, tmp_path: Path, **kwargs: object) -> list[str]:
    return backend.arguments(spec(tmp_path, ["true"], **kwargs), "auger-test")


# --- argument rules -------------------------------------------------------------------


@pytest.mark.parametrize("backend", [AppleContainer(), Podman(), Docker()])
def test_the_repository_is_mounted_read_only(backend: OciSandbox, tmp_path: Path) -> None:
    assert f"{tmp_path}:/work:ro" in arguments(backend, tmp_path)


@pytest.mark.parametrize("backend", [AppleContainer(), Podman(), Docker()])
def test_there_is_no_network_by_default(backend: OciSandbox, tmp_path: Path) -> None:
    line = arguments(backend, tmp_path)
    assert line[line.index("--network") + 1] == "none"


@pytest.mark.parametrize("backend", [AppleContainer(), Podman(), Docker()])
def test_a_nat_run_asks_for_it_explicitly(backend: OciSandbox, tmp_path: Path) -> None:
    assert "--network" not in arguments(backend, tmp_path, network=Network.NAT)


@pytest.mark.parametrize("backend", [AppleContainer(), Podman(), Docker()])
def test_the_process_drops_every_capability(backend: OciSandbox, tmp_path: Path) -> None:
    line = arguments(backend, tmp_path)
    assert line[line.index("--cap-drop") + 1] == "ALL"
    assert line[line.index("--user") + 1] == "65534:65534"


@pytest.mark.parametrize("backend", [AppleContainer(), Podman(), Docker()])
def test_the_container_is_removed_and_named(backend: OciSandbox, tmp_path: Path) -> None:
    line = arguments(backend, tmp_path)
    assert "--rm" in line
    assert line[line.index("--name") + 1] == "auger-test"


def test_apple_container_turns_off_name_resolution(tmp_path: Path) -> None:
    assert "--no-dns" in arguments(AppleContainer(), tmp_path)


def test_podman_refuses_new_privileges(tmp_path: Path) -> None:
    line = arguments(Podman(), tmp_path)
    assert line[line.index("--security-opt") + 1] == "no-new-privileges"


def test_the_scratch_size_is_capped_where_the_runtime_allows_it(tmp_path: Path) -> None:
    line = arguments(Podman(), tmp_path, scratch_mb=64)
    assert line[line.index("--tmpfs") + 1] == "/scratch:rw,size=64m,noexec,nosuid"


def test_the_environment_reaches_the_container(tmp_path: Path) -> None:
    line = arguments(AppleContainer(), tmp_path, env={"AUGER_JOB": "j1"})
    assert line[line.index("--env") + 1] == "AUGER_JOB=j1"


def test_the_command_comes_last(tmp_path: Path) -> None:
    line = AppleContainer().arguments(spec(tmp_path, ["semgrep", "--json"]), "auger-test")
    assert line[-3:] == [IMAGE, "semgrep", "--json"]


def test_a_missing_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        AppleContainer().run(spec(tmp_path / "gone", ["true"]))


# --- backend choice -------------------------------------------------------------------


def test_it_prefers_a_container_runtime_over_seatbelt() -> None:
    selection = select()
    if shutil.which("container") or shutil.which("podman") or shutil.which("docker"):
        assert not selection.degraded
    else:
        assert selection.warning == SEATBELT_WARNING


def test_a_degraded_choice_carries_a_warning() -> None:
    assert Selection(Seatbelt(), SEATBELT_WARNING).degraded is True
    assert Selection(AppleContainer()).degraded is False


# --- seatbelt -------------------------------------------------------------------------


@pytest.mark.skipif(not Seatbelt().available(), reason="sandbox-exec is missing")
def test_seatbelt_cannot_write_to_the_repository(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("secret", encoding="utf-8")
    result = Seatbelt().run(spec(tmp_path, ["sh", "-c", "echo x > file.txt"]))
    assert result.exit_code != 0
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "secret"


@pytest.mark.skipif(not Seatbelt().available(), reason="sandbox-exec is missing")
def test_seatbelt_can_read_the_repository(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("secret", encoding="utf-8")
    result = Seatbelt().run(spec(tmp_path, ["cat", "file.txt"]))
    assert result.stdout.strip() == "secret"


@pytest.mark.skipif(not Seatbelt().available(), reason="sandbox-exec is missing")
def test_seatbelt_can_write_to_its_scratch(tmp_path: Path) -> None:
    result = Seatbelt().run(spec(tmp_path, ["sh", "-c", 'touch "$AUGER_SCRATCH/x" && echo ok']))
    assert result.stdout.strip() == "ok"


@pytest.mark.skipif(not Seatbelt().available(), reason="sandbox-exec is missing")
def test_seatbelt_has_no_network(tmp_path: Path) -> None:
    result = Seatbelt().run(spec(tmp_path, ["/usr/bin/nc", "-z", "-w", "2", "1.1.1.1", "443"]))
    assert result.exit_code != 0


def test_seatbelt_refuses_a_run_that_asks_for_the_network(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        Seatbelt().run(spec(tmp_path, ["true"], network=Network.NAT))


def test_a_runtime_outside_the_graphical_path_is_still_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An application started at login inherits launchd's PATH, which holds no
    package manager directory. Without this, a Homebrew install looks absent."""
    from auger.sandbox import which

    brew = tmp_path / "brew-bin"
    brew.mkdir()
    tool = brew / "container"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(which, "EXTRA_PATHS", (str(brew),))
    assert which.find("container") == str(tool)


def test_a_tool_that_is_nowhere_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auger.sandbox import which

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(which, "EXTRA_PATHS", (str(tmp_path / "also-empty"),))
    assert which.find("container") is None


def test_the_command_uses_the_full_path(tmp_path: Path) -> None:
    """A narrow PATH would otherwise make the runtime unreachable at run time too."""
    line = AppleContainer().arguments(spec(tmp_path, ["true"]), "auger-test")
    assert line[0].endswith("container")

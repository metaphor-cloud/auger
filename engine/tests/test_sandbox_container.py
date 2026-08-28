"""Rules that a command line flag alone cannot prove.

These run a real container. They skip when no runtime is installed or when the test image
is not present, because a test must never depend on a network fetch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reviewrig.sandbox import RunResult, RunSpec, Sandbox, select
from reviewrig.sandbox.seatbelt import Seatbelt

IMAGE = "python:3.12-alpine"


@pytest.fixture(scope="session")
def container(tmp_path_factory: pytest.TempPathFactory) -> Sandbox:
    sandbox = select().sandbox
    if sandbox.name == Seatbelt.name:
        pytest.skip("no container runtime is installed")
    probe = tmp_path_factory.mktemp("probe")
    try:
        result = sandbox.run(
            RunSpec(repository=probe, command=["true"], image=IMAGE, timeout_seconds=90)
        )
    except Exception as error:
        pytest.skip(f"{sandbox.name} cannot run: {error}")
    if not result.ok:
        pytest.skip(f"{IMAGE} is not available locally: {result.stderr[:200]}")
    return sandbox


def run(sandbox: Sandbox, repository: Path, command: list[str], **kwargs: object) -> RunResult:
    return sandbox.run(
        RunSpec(repository=repository, command=command, image=IMAGE, **kwargs)  # type: ignore[arg-type]
    )


def test_it_reads_the_repository(container: Sandbox, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("secret", encoding="utf-8")
    result = run(container, tmp_path, ["cat", "/work/file.txt"])
    assert result.stdout.strip() == "secret"


def test_it_cannot_write_to_the_repository(container: Sandbox, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("secret", encoding="utf-8")
    result = run(container, tmp_path, ["sh", "-c", "echo tampered > /work/file.txt"])
    assert result.exit_code != 0
    assert (tmp_path / "file.txt").read_text(encoding="utf-8") == "secret"


def test_it_can_write_to_the_scratch(container: Sandbox, tmp_path: Path) -> None:
    result = run(container, tmp_path, ["sh", "-c", "touch /scratch/x && echo ok"])
    assert result.stdout.strip() == "ok"


def test_it_runs_as_a_user_with_no_privileges(container: Sandbox, tmp_path: Path) -> None:
    result = run(container, tmp_path, ["id", "-u"])
    assert result.stdout.strip() == "65534"


def test_it_has_no_network(container: Sandbox, tmp_path: Path) -> None:
    """The whole guarantee rests on this. A container that can reach the internet can
    send the user's code anywhere."""
    result = run(
        container,
        tmp_path,
        [
            "python",
            "-c",
            "import urllib.request;urllib.request.urlopen('http://1.1.1.1',timeout=5)",
        ],
        timeout_seconds=60,
    )
    assert result.exit_code != 0


def test_a_run_that_overruns_is_killed(container: Sandbox, tmp_path: Path) -> None:
    result = run(container, tmp_path, ["sleep", "60"], timeout_seconds=8)
    assert result.timed_out is True
    assert result.exit_code == 124

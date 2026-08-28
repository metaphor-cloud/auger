from __future__ import annotations

import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest

from reviewrig.config.schema import Backend
from reviewrig.llm import Supervisor, discover, probe, probe_all
from reviewrig.llm.supervisor import port_of
from tests.helpers import FakeModelServer

Serve = Callable[[object], Awaitable[str]]


@pytest.fixture
async def client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def test_it_reports_the_models_a_server_holds(
    serve: Serve, client: httpx.AsyncClient
) -> None:
    fake = FakeModelServer()
    fake.models = ["gpt-oss-120b"]
    base = await serve(fake.app())
    async with client:
        health = await probe(client, "review", Backend(url=f"{base}/v1"))
    assert health.up is True
    assert health.models == ("gpt-oss-120b",)


async def test_a_server_that_is_not_there_reports_why(client: httpx.AsyncClient) -> None:
    async with client:
        health = await probe(client, "review", Backend(url="http://127.0.0.1:1/v1"))
    assert health.up is False
    assert health.reason


async def test_it_probes_every_backend(serve: Serve, client: httpx.AsyncClient) -> None:
    base = await serve(FakeModelServer().app())
    backends = {
        "up": Backend(url=f"{base}/v1"),
        "down": Backend(url="http://127.0.0.1:1/v1"),
    }
    async with client:
        health = await probe_all(client, backends)
    assert health["up"].up is True
    assert health["down"].up is False


async def test_discovery_finds_a_server_the_user_already_runs(
    serve: Serve, client: httpx.AsyncClient
) -> None:
    base = await serve(FakeModelServer().app())
    async with client:
        found = await discover(client, ports=[port_of(base), 1])
    assert [health.url for health in found] == [f"{base}/v1"]


def test_it_will_not_start_without_a_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The message must say what to do, not what is missing."""
    from reviewrig.sandbox import which

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    health = Supervisor(tmp_path / "models").start(
        "review", Backend(managed=True, model_file="m.gguf")
    )
    assert health.up is False
    assert "Set up" in (health.reason or "")


def test_it_prefers_the_runtime_the_rig_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first run has nothing on PATH, so the rig's own copy is the only one."""
    from reviewrig.sandbox import which

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    own = tmp_path / "runtime" / "b1" / "build" / "bin" / "llama-server"
    own.parent.mkdir(parents=True)
    own.write_text("#!/bin/sh\n", encoding="utf-8")
    own.chmod(0o755)
    assert Supervisor(tmp_path / "models").server_command() == str(own)


def test_it_will_not_start_without_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_server(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    health = Supervisor(tmp_path).start("review", Backend(managed=True, model_file="missing.gguf"))
    assert health.up is False
    assert "weights not found" in (health.reason or "")


def test_the_command_carries_the_port_and_the_batch_depth(tmp_path: Path) -> None:
    backend = Backend(
        url="http://127.0.0.1:8099/v1", model_file="m.gguf", max_concurrent=6, args=["--embedding"]
    )
    arguments = Supervisor(tmp_path).arguments(backend, "llama-server", tmp_path / "m.gguf")
    assert arguments[arguments.index("--port") + 1] == "8099"
    assert arguments[arguments.index("--parallel") + 1] == "6"
    assert arguments[arguments.index("--host") + 1] == "127.0.0.1"
    assert arguments[-1] == "--embedding"


def test_it_starts_and_stops_a_managed_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_server(tmp_path)
    (tmp_path / "m.gguf").write_bytes(b"weights")
    monkeypatch.setenv("PATH", str(tmp_path))
    supervisor = Supervisor(tmp_path)
    health = supervisor.start("review", Backend(managed=True, model_file="m.gguf"))
    assert health.reason == "starting"
    process = supervisor.running["review"].process
    assert process.poll() is None
    supervisor.stop_all()
    assert process.poll() is not None
    assert supervisor.running == {}


def test_starting_twice_reuses_the_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_server(tmp_path)
    (tmp_path / "m.gguf").write_bytes(b"weights")
    monkeypatch.setenv("PATH", str(tmp_path))
    supervisor = Supervisor(tmp_path)
    backend = Backend(managed=True, model_file="m.gguf")
    supervisor.start("review", backend)
    first = supervisor.running["review"].process.pid
    supervisor.start("review", backend)
    assert supervisor.running["review"].process.pid == first
    supervisor.stop_all()


async def test_it_leaves_a_running_server_alone(serve: Serve, client: httpx.AsyncClient) -> None:
    """A server the user already runs holds the model they chose, and may be warm."""
    base = await serve(FakeModelServer().app())
    supervisor = Supervisor(Path("/nonexistent"))
    async with client:
        health = await supervisor.ensure(
            client, {"review": Backend(url=f"{base}/v1", managed=True, model_file="m.gguf")}
        )
    assert health["review"].up is True
    assert supervisor.running == {}


def _fake_server(directory: Path) -> Path:
    """A `llama-server` that just sleeps, so a start can be observed and stopped."""
    path = directory / "llama-server"
    path.write_text("#!/bin/sh\nsleep 300\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path

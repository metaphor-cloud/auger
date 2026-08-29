"""A watcher that is never started never runs, and nothing else would say so."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from auger.llm import Health
from auger.rig import Rig
from auger.schedule import watch, watch_audits, watch_forges, watch_models, watch_verify
from auger.settings import Settings
from tests.helpers import git_commit, git_init


def test_every_watcher_is_in_the_list() -> None:
    """The list is what `start_background` iterates. Adding one here is the only step."""
    assert set(Rig.WATCHERS) == {watch, watch_forges, watch_audits, watch_models, watch_verify}


@pytest.fixture
def tree(tmp_path: Path, home: Path) -> Path:
    tree = tmp_path / "tree"
    path = git_init(tree / "alpha", remote="git@github.com:acme/alpha.git")
    git_commit(path, {"a.py": "x = 1\n"}, "one")
    (home / "config.toml").write_text(
        f"""
[[roots]]
path = "{tree}"

[defaults]
idle_seconds = 0
audit_hours = 1

[schedule]
poll_seconds = 3600
audit_poll_seconds = 60
""",
        encoding="utf-8",
    )
    return tree


async def test_the_rig_starts_one_task_per_watcher(home: Path, token: str, tree: Path) -> None:
    rig = Rig(Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home))
    try:
        rig.scan()
        await rig.start_background()
        assert len(rig._background) == len(Rig.WATCHERS)
    finally:
        await rig.aclose()


async def test_nothing_reviews_until_the_user_presses_play(
    home: Path, token: str, tree: Path
) -> None:
    """The window opens stopped. The watchers still fill the queue, so it shows what
    is waiting, and the machine stays quiet until somebody asks for the work."""
    rig = Rig(Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home))
    try:
        rig.scan()
        await rig.start_background()
        assert rig.scheduler.paused is True
        rig.scheduler.resume()
        assert rig.scheduler.paused is False
    finally:
        await rig.aclose()


async def test_an_audit_is_queued_without_anyone_asking(home: Path, token: str, tree: Path) -> None:
    """This is the check that would have caught an audit watcher that never started."""
    rig = Rig(Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home))
    rig.scheduler.pause()  # Queue the work, and do not run it.
    try:
        rig.scan()
        await rig.start_background()
        for _ in range(50):
            if rig.scheduler.pending:
                break
            await asyncio.sleep(0.05)
        targets = [
            rig.scheduler.queue.get_nowait().target for _ in range(rig.scheduler.queue.qsize())
        ]
        assert "audit" in targets
    finally:
        await rig.aclose()


async def test_a_managed_backend_is_started_before_any_review(
    home: Path, token: str, tree: Path
) -> None:
    """A review that runs before its model is up fails and is recorded as a failure."""
    import httpx

    from auger.api import create_app

    (home / "config.toml").write_text(
        f'[[roots]]\npath = "{tree}"\n\n'
        '[backend.local-review]\nurl = "http://127.0.0.1:1/v1"\nmanaged = true\n',
        encoding="utf-8",
    )
    rig = Rig(Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home))
    started: list[str] = []
    real = rig.ensure_models

    async def spy() -> dict[str, Health]:
        started.append("ensure")
        return await real()

    rig.ensure_models = spy  # type: ignore[method-assign]
    app = create_app(rig)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://engine") as http:
        # The lifespan runs the boot task. Give it a moment to reach the model step.
        async with app.router.lifespan_context(app):
            for _ in range(50):
                if started:
                    break
                await asyncio.sleep(0.05)
            assert started == ["ensure"]
        await http.aclose()


async def test_a_managed_server_that_died_is_not_reported_as_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record of a dead process would report a server that is gone as running, and
    every review would fail against nothing."""
    import contextlib
    import stat
    import subprocess

    from auger.config.schema import Backend
    from auger.llm import Supervisor
    from auger.sandbox import which

    server = tmp_path / "llama-server"
    server.write_text("#!/bin/sh\nsleep 300\n", encoding="utf-8")
    server.chmod(server.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "m.gguf").write_bytes(b"weights")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())

    supervisor = Supervisor(tmp_path)
    backend = Backend(managed=True, model_file="m.gguf")
    supervisor.start("review", backend)
    process = supervisor.running["review"].process
    process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)

    health = supervisor.start("review", backend)
    assert health.reason == "starting"
    assert supervisor.running["review"].process.pid != process.pid
    supervisor.stop_all()


def test_the_model_watcher_reports_and_starts_nothing() -> None:
    """A run starts the backend it needs. Starting one here as well would take back
    the memory that Unload just gave up."""
    import inspect

    from auger.schedule import watch_models

    assert watch_models in Rig.WATCHERS
    body = inspect.getsource(watch_models)
    assert "ensure_models" not in body
    assert "check_models" in body

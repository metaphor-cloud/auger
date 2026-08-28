"""A watcher that is never started never runs, and nothing else would say so."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reviewrig.llm import Health
from reviewrig.rig import Rig
from reviewrig.schedule import watch, watch_audits, watch_forges
from reviewrig.settings import Settings
from tests.helpers import git_commit, git_init


def test_every_watcher_is_in_the_list() -> None:
    """The list is what `start_background` iterates. Adding one here is the only step."""
    assert set(Rig.WATCHERS) == {watch, watch_forges, watch_audits}


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

    from reviewrig.api import create_app

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

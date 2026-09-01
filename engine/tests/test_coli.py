"""The second engine: what it needs, how it is installed, and how it is started."""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response

from auger.config.schema import Backend, Config, JobClass, Profile, ProfileEntry
from auger.downloads import Manager
from auger.llm import coli
from auger.llm.gateway import EngineMismatchError, Gateway
from auger.llm.setup import coli_backend_for
from auger.llm.supervisor import Supervisor
from auger.net import Allowlist, download

Serve = Callable[[object], Awaitable[str]]


def tarball(members: dict[str, bytes]) -> bytes:
    """A flat archive, which is the shape this engine publishes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


ARCHIVE = tarball(
    {"coli": b"#!/usr/bin/env python3\n", "colibri": b"\x7fELF", "qwen36": b"\x7fELF"}
)


@pytest.fixture
def release_server() -> tuple[FastAPI, bytes]:
    """The releases API and the asset, with the digest the API publishes."""
    import hashlib

    app = FastAPI()
    digest = hashlib.sha256(ARCHIVE).hexdigest()
    wanted = coli.asset_name() or "macos-arm64"

    @app.get("/releases")
    async def releases() -> list[dict[str, object]]:
        return [
            {
                "tag_name": "v9.9.9",
                "assets": [
                    {
                        "name": f"colibri-v9.9.9-{wanted}.tar.gz",
                        "digest": f"sha256:{digest}",
                        "size": len(ARCHIVE),
                        "browser_download_url": "REPLACED",
                    }
                ],
            }
        ]

    @app.get("/asset.tar.gz")
    async def asset() -> Response:
        return Response(content=ARCHIVE, media_type="application/gzip")

    return app, ARCHIVE


# --- what it needs -------------------------------------------------------------------


def test_it_says_what_is_missing_before_a_download_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its launcher is a Python script. Finding that out after fetching it is finding
    out too late."""
    monkeypatch.setattr(coli, "python", lambda: "")
    ready = coli.readiness(tmp_path)
    assert not ready.usable
    assert any("Python 3" in one for one in ready.problems)


def test_a_platform_with_no_build_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coli, "asset_name", lambda: None)
    ready = coli.readiness(tmp_path)
    assert not ready.supported
    assert any("no build" in one for one in ready.problems)


def test_nothing_is_installed_until_it_is(tmp_path: Path) -> None:
    assert coli.installed(tmp_path) is None
    assert coli.readiness(tmp_path).installed == ""


# --- installing ----------------------------------------------------------------------


async def test_installing_unpacks_the_launcher_and_makes_it_runnable(
    serve: Serve,
    tmp_path: Path,
    release_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = release_server
    base = await serve(app)
    monkeypatch.setattr(coli, "RELEASES", f"{base}/releases")
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(coli, "python", lambda: "/usr/bin/python3")
    original = coli.latest_release

    async def patched(http: httpx.AsyncClient, log: object = None) -> coli.Release:
        release = await original(http, None)
        return coli.Release(
            tag=release.tag,
            url=f"{base}/asset.tar.gz",
            sha256=release.sha256,
            size_bytes=release.size_bytes,
            asset=release.asset,
        )

    monkeypatch.setattr(coli, "latest_release", patched)
    downloads = Manager(tmp_path)
    try:
        async with download.client() as http:
            launcher = await coli.install(http, tmp_path, downloads)
    finally:
        await downloads.aclose()

    assert launcher.name == coli.LAUNCHER
    assert launcher.is_file()
    assert launcher.stat().st_mode & 0o111, "the launcher has to be runnable"
    assert (launcher.parent / coli.ENGINE).stat().st_mode & 0o111
    # And the archive is not left behind.
    assert not list(coli.runtime_dir(tmp_path).glob("*.tar.gz"))
    assert coli.installed(tmp_path) == launcher


async def test_it_refuses_to_install_when_python_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coli, "python", lambda: "")
    downloads = Manager(tmp_path)
    try:
        async with download.client() as http:
            with pytest.raises(coli.ColiError, match="Python 3"):
                await coli.install(http, tmp_path, downloads)
    finally:
        await downloads.aclose()


# --- starting it ---------------------------------------------------------------------


def test_the_command_line_is_this_engine_s_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory of weights and a slot count, not a file and a shared context size."""
    monkeypatch.setattr(coli, "python", lambda: "/usr/bin/python3")
    supervisor = Supervisor(tmp_path / "models", log_dir=tmp_path / "logs")
    backend = Backend(
        url="http://127.0.0.1:1345/v1",
        managed=True,
        engine="coli",
        model_file="qwen3.6-35b-a3b",
        max_concurrent=1,
    )
    weights = tmp_path / "models" / "qwen3.6-35b-a3b"
    arguments = supervisor.arguments(backend, "/runtime/coli/v1/coli", weights, context=8192)
    assert arguments[0] == "/usr/bin/python3"
    assert arguments[1:4] == ["/runtime/coli/v1/coli", "serve", "--model"]
    assert arguments[4] == str(weights)
    assert "--port" in arguments and arguments[arguments.index("--port") + 1] == "1345"
    assert "--kv-slots" in arguments
    # Its own answer ceiling is a thousand tokens, which cuts a review's findings off.
    assert "--max-tokens" in arguments
    assert int(arguments[arguments.index("--max-tokens") + 1]) >= 8192
    assert "--ctx-size" not in arguments, "that is the other engine's flag"


def test_the_default_engine_is_unchanged(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path / "models", log_dir=tmp_path / "logs")
    backend = Backend(url="http://127.0.0.1:1337/v1", managed=True, model_file="model.gguf")
    arguments = supervisor.arguments(backend, "/llama-server", tmp_path / "model.gguf", 8192)
    assert arguments[0] == "/llama-server"
    assert "--ctx-size" in arguments
    assert "serve" not in arguments


def test_a_missing_engine_says_which_one(tmp_path: Path) -> None:
    supervisor = Supervisor(tmp_path / "models", log_dir=tmp_path / "logs")
    backend = Backend(url="http://127.0.0.1:1345/v1", managed=True, engine="coli", model_file="m")
    health = supervisor.start("coli-review", backend)
    assert not health.up
    assert coli.NAME in (health.reason or "")


def test_the_context_is_not_read_from_a_directory(tmp_path: Path) -> None:
    """These weights are shards with no header to read a trained context out of, and the
    engine streams them rather than holding them, so the other engine's arithmetic does
    not apply."""
    supervisor = Supervisor(tmp_path / "models", log_dir=tmp_path / "logs")
    weights = tmp_path / "models" / "a-model"
    weights.mkdir(parents=True)
    (weights / "config.json").write_text(json.dumps({"model_type": "qwen3_5_moe"}))
    backend = Backend(
        url="http://127.0.0.1:1345/v1", managed=True, engine="coli", model_file="a-model"
    )
    assert supervisor.context_for("coli-review", backend, weights, []) > 0
    backend = backend.model_copy(update={"context_tokens": 32768})
    assert supervisor.context_for("coli-review", backend, weights, []) == 32768


# --- what it cannot do ---------------------------------------------------------------


def test_a_profile_that_asks_it_to_embed_is_refused_up_front() -> None:
    """A run per repository failing with a 404 is a worse report than one sentence."""
    config = Config(
        backend={"coli-review": Backend(url="http://127.0.0.1:1345/v1", engine="coli")},
        profile={
            "balanced": Profile(
                review=ProfileEntry(backend="coli-review"),
                embed=ProfileEntry(backend="coli-review"),
            )
        },
    )
    gateway = Gateway(config, Allowlist())
    assert gateway.resolve(JobClass.REVIEW, "balanced").name == "coli-review"
    with pytest.raises(EngineMismatchError, match="chat only"):
        gateway.resolve(JobClass.EMBED, "balanced")


def test_pointing_a_job_class_at_it_writes_a_server_of_its_own() -> None:
    from auger.llm.catalog import COLI_MODELS

    config = Config()
    name = coli_backend_for(config, COLI_MODELS[0], JobClass.REVIEW)
    backend = config.backend[name]
    assert backend.engine == "coli"
    assert backend.managed is True
    # A directory, not a file.
    assert backend.model_file == COLI_MODELS[0].name
    # It serves one generation at a time and queues the rest.
    assert backend.max_concurrent == 1
    # And the port is not the other engine's.
    assert "1337" not in backend.url
    assert config.profile["balanced"].review.backend == name

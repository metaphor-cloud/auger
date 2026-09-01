"""The rig brings its own runtime and its own weights.

Point it at your code and it works: that is the promise, and it fails if a first run
begins with a shopping list.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from auger.config.schema import Config
from auger.downloads import Manager
from auger.llm import catalog, runtime, setup
from auger.llm.catalog import CatalogError
from auger.net import download
from auger.net.download import DownloadError, allowed, fetch, safe_url

Serve = Callable[[object], Awaitable[str]]


def tarball(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# --- the download host policy --------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://huggingface.co/x", True),
        ("https://api.github.com/x", True),
        ("https://us.aws.cdn.hf.co/x", True),
        ("https://release-assets.githubusercontent.com/x", True),
        ("https://evil.example/x", False),
        ("https://hf.co.evil.example/x", False),
        ("", False),
    ],
)
def test_only_a_delivery_host_may_serve_a_download(url: str, expected: bool) -> None:
    assert allowed(url) is expected


def test_a_signed_url_is_not_logged_whole() -> None:
    """A delivery URL carries a credential in its query."""
    safe = safe_url("https://us.aws.cdn.hf.co/file.gguf?Signature=secret&Key-Pair-Id=abc")
    assert "secret" not in safe
    assert safe.endswith("/file.gguf")


# --- fetching ------------------------------------------------------------------------


@pytest.fixture
def file_server() -> tuple[FastAPI, bytes]:
    body = b"weights" * 1000
    app = FastAPI()

    @app.get("/model.gguf")
    async def model() -> Response:
        return Response(content=body, media_type="application/octet-stream")

    @app.api_route("/redirect-away", methods=["GET", "HEAD"])
    async def away() -> Response:
        return Response(status_code=302, headers={"location": "https://evil.example/model.gguf"})

    return app, body


async def test_it_keeps_a_file_that_matches_its_checksum(
    serve: Serve,
    tmp_path: Path,
    file_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, body = file_server
    base = await serve(app)
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    async with download.client() as http:
        path = await fetch(http, f"{base}/model.gguf", tmp_path / "m.gguf", sha(body))
    assert path.read_bytes() == body


async def test_a_wrong_checksum_leaves_no_file(
    serve: Serve,
    tmp_path: Path,
    file_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = file_server
    base = await serve(app)
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    async with download.client() as http:
        with pytest.raises(DownloadError, match="checksum"):
            await fetch(http, f"{base}/model.gguf", tmp_path / "m.gguf", "00" * 32)
    assert list(tmp_path.iterdir()) == []


async def test_a_download_with_no_checksum_is_refused(
    serve: Serve,
    tmp_path: Path,
    file_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without one there is nothing to check the delivery host against."""
    app, _ = file_server
    base = await serve(app)
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    async with download.client() as http:
        with pytest.raises(DownloadError, match="checksum"):
            await fetch(http, f"{base}/model.gguf", tmp_path / "m.gguf", "")


async def test_a_redirect_off_the_allowlist_is_refused(
    serve: Serve,
    tmp_path: Path,
    file_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every hop is checked, not only the first."""
    app, _ = file_server
    base = await serve(app)
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(download, "DELIVERY_SUFFIXES", ())
    async with download.client() as http:
        with pytest.raises(DownloadError, match="not a download host"):
            await fetch(http, f"{base}/redirect-away", tmp_path / "m.gguf", "00" * 32)


# --- the runtime ---------------------------------------------------------------------


def test_it_knows_which_build_this_machine_needs() -> None:
    name = runtime.asset_name()
    assert name in {"macos-arm64", "macos-x64", "ubuntu-arm64", "ubuntu-x64", None}


@pytest.fixture
def release_server() -> tuple[FastAPI, bytes]:
    archive = tarball({"build/bin/llama-server": b"#!/bin/sh\n", "build/bin/libggml.dylib": b"x"})
    app = FastAPI()

    @app.get("/repos/ggml-org/llama.cpp/releases")
    async def releases() -> JSONResponse:
        return JSONResponse(
            [
                {"tag_name": "v0.3.0", "assets": [{"name": "nightly-tag.txt", "size": 1}]},
                {
                    "tag_name": "b10665",
                    "assets": [
                        {
                            "name": f"llama-b10665-bin-{runtime.asset_name()}.tar.gz",
                            "size": len(archive),
                            "digest": f"sha256:{sha(archive)}",
                            "browser_download_url": "http://127.0.0.1:PORT/asset.tar.gz",
                        }
                    ],
                },
            ]
        )

    @app.get("/asset.tar.gz")
    async def asset() -> Response:
        return Response(content=archive, media_type="application/octet-stream")

    return app, archive


async def test_it_skips_a_release_that_carries_no_build(
    serve: Serve, release_server: tuple[FastAPI, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The newest tagged release sometimes holds nothing but a text file."""
    app, _ = release_server
    base = await serve(app)
    monkeypatch.setattr(runtime, "RELEASES", f"{base}/repos/ggml-org/llama.cpp/releases")
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    async with download.client() as http:
        release = await runtime.latest_release(http)
    assert release.tag == "b10665"
    assert release.sha256.startswith("sha256:")


async def test_it_installs_and_finds_the_server(
    serve: Serve,
    tmp_path: Path,
    release_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _archive = release_server
    base = await serve(app)
    monkeypatch.setattr(runtime, "RELEASES", f"{base}/repos/ggml-org/llama.cpp/releases")
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))

    original = runtime.latest_release

    async def patched(http: httpx.AsyncClient, log: object = None) -> runtime.Release:
        release = await original(http, None)
        return runtime.Release(
            tag=release.tag,
            url=f"{base}/asset.tar.gz",
            sha256=release.sha256,
            size_bytes=release.size_bytes,
            asset=release.asset,
        )

    monkeypatch.setattr(runtime, "latest_release", patched)
    downloads = Manager(tmp_path)
    try:
        async with download.client() as http:
            server = await runtime.install(http, tmp_path, downloads)
    finally:
        await downloads.aclose()
    assert server.name == "llama-server"
    assert server.is_file()
    assert runtime.resolve(tmp_path) is not None


async def test_an_archive_that_would_escape_is_refused(tmp_path: Path) -> None:
    """A member named `../..` must not write outside the runtime directory."""
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(tarball({"../escaped": b"x"}))
    with pytest.raises((tarfile.TarError, OSError)):
        runtime._extract(archive, tmp_path / "into")
    assert not (tmp_path / "escaped").exists()


# --- the catalogue -------------------------------------------------------------------


def test_a_machine_with_room_to_spare_still_gets_the_default_reviewer() -> None:
    """Largest that fits is not the same question as best pairing. Muse Glimmer leaves
    room to swap in a second model of its own size, which is what makes the check
    worth having. A larger reviewer is a deliberate choice, not a default."""
    assert catalog.recommended_review_model(96).name == "Muse-Glimmer-30B"
    assert catalog.by_name("gpt-oss-120b").memory_gb <= 96, "the larger one is still offered"


def test_the_measured_embedder_wins_over_the_larger_one() -> None:
    """Largest that fits is the wrong rule here: the code embedder was measured on real
    retrieval and the larger general purpose one was not. Switching also drops every
    vector, because the dimension changes."""
    assert catalog.recommended_embed_model(200).name == "nomic-embed-code"
    assert (
        catalog.by_name("Qwen3-Embedding-8B").memory_gb
        > catalog.by_name("nomic-embed-code").memory_gb
    ), "the larger one is offered, and is not the default"


def test_the_catalogue_stays_ordered_largest_first() -> None:
    """`_largest_that_fits` walks the list and takes the first that fits, so an entry
    added in the wrong place would hand a big machine a small model."""
    for group in (catalog.REVIEW_MODELS, catalog.ADVERSARY_MODELS, catalog.EMBED_MODELS):
        sizes = [choice.memory_gb for choice in group]
        assert sizes == sorted(sizes, reverse=True), [choice.name for choice in group]


def test_every_model_comes_from_a_named_publisher() -> None:
    """A community re-upload can vanish or change under us. These are the accounts that
    publish the weights or maintain llama.cpp itself."""
    allowed = {"ggml-org", "google", "meta-models", "Qwen", "nomic-ai"}
    for choice in catalog.CATALOG:
        assert choice.repo.split("/")[0] in allowed, choice.repo


def test_a_laptop_gets_a_model_that_fits() -> None:
    assert catalog.recommended_review_model(11).name == "gemma-4-12b-qat"


def test_a_tiny_machine_still_gets_a_model() -> None:
    """Returning nothing would leave the user with no way forward."""
    assert catalog.recommended_review_model(1).name == "gemma-4-12b-qat"


def test_this_machine_reports_its_memory() -> None:
    assert catalog.usable_memory_gb() > 0


def test_an_unknown_model_says_so() -> None:
    with pytest.raises(CatalogError, match="ghost"):
        catalog.by_name("ghost")


async def test_the_checksum_comes_from_the_repository(
    serve: Serve, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()

    @app.get("/api/models/{owner}/{name}/tree/main")
    async def tree(owner: str, name: str) -> JSONResponse:
        return JSONResponse(
            [{"path": catalog.SMALL_EMBED_MODEL.filename, "size": 42, "lfs": {"oid": "ab" * 32}}]
        )

    base = await serve(app)
    monkeypatch.setattr(catalog, "HUGGINGFACE", base)
    async with download.client() as http:
        resolved = await catalog.resolve(http, catalog.SMALL_EMBED_MODEL)
    assert resolved.sha256 == "ab" * 32
    assert resolved.size_bytes == 42


async def test_a_file_with_no_checksum_is_refused(
    serve: Serve, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()

    @app.get("/api/models/{owner}/{name}/tree/main")
    async def tree(owner: str, name: str) -> JSONResponse:
        return JSONResponse([{"path": catalog.SMALL_EMBED_MODEL.filename, "size": 42}])

    base = await serve(app)
    monkeypatch.setattr(catalog, "HUGGINGFACE", base)
    async with download.client() as http:
        with pytest.raises(CatalogError, match="checksum"):
            await catalog.resolve(http, catalog.SMALL_EMBED_MODEL)


# --- the plan ------------------------------------------------------------------------


def test_the_plan_holds_a_reviewer_and_an_embedder() -> None:
    """No reranker.

    Measured on this repository, reranking cut recall at 12 from 0.686 to 0.373, so
    fetching one by default would cost a download and give a worse review.
    """
    assert [choice.job_class.value for choice in setup.plan(96)] == ["review", "embed"]


def test_a_machine_with_room_gets_the_code_embedder() -> None:
    """It raised recall at 12 from 0.613 to 0.686 on this repository."""
    assert catalog.recommended_embed_model(96).name == "nomic-embed-code"


def test_a_small_machine_gets_the_small_embedder() -> None:
    assert catalog.recommended_embed_model(4).name == "Qwen3-Embedding-0.6B"


def test_it_points_the_managed_backends_at_what_it_fetched() -> None:
    config = Config()
    review, embed = setup.plan(96)
    setup.apply_to_config(config, review, embed)
    assert config.backend["local-review"].managed is True
    assert config.backend["local-review"].model_file == review.filename
    assert config.backend["local-embed"].model_file == embed.filename
    assert "--embedding" in config.backend["local-embed"].args


def test_a_setup_leaves_reranking_off() -> None:
    """Turning it on measurably made retrieval worse."""
    config = Config()
    review, embed = setup.plan(96)
    setup.apply_to_config(config, review, embed)
    assert config.profile["balanced"].rerank.backend == ""
    assert "local-rerank" not in config.backend


def test_a_reranker_can_still_be_configured_by_hand() -> None:
    """It stays available, because a better reranker may earn its place later."""
    config = Config()
    review, embed = setup.plan(96)
    setup.apply_to_config(config, review, embed, catalog.RERANK_MODEL)
    assert config.backend["local-rerank"].model_file == catalog.RERANK_MODEL.filename
    assert "--reranking" in config.backend["local-rerank"].args
    assert config.profile["balanced"].rerank.backend == "local-rerank"


async def test_a_setup_that_cannot_reach_a_release_reports_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auger.sandbox import which

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    monkeypatch.setattr(runtime, "RELEASES", "http://127.0.0.1:1/releases")
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    steps: list[setup.Step] = []
    result = await setup.install(tmp_path, Config(), None, None, None, steps.append)
    assert result.ok is False
    assert result.error
    assert steps[-1].stage == "failed"


# --- resuming ------------------------------------------------------------------------


@pytest.fixture
def ranged_server() -> tuple[FastAPI, bytes]:
    """A host that honours Range, like a real delivery host."""
    body = bytes(range(256)) * 400
    app = FastAPI()

    @app.get("/model.gguf")
    async def model(request: Request) -> Response:
        header = request.headers.get("range", "")
        if header.startswith("bytes="):
            start = int(header.removeprefix("bytes=").split("-")[0])
            return Response(
                content=body[start:],
                status_code=206,
                media_type="application/octet-stream",
                headers={"content-range": f"bytes {start}-{len(body) - 1}/{len(body)}"},
            )
        return Response(content=body, media_type="application/octet-stream")

    return app, body


async def test_a_half_finished_download_carries_on(
    serve: Serve,
    tmp_path: Path,
    ranged_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weights are tens of gigabytes. Starting again at ninety per cent finishes nothing."""
    app, body = ranged_server
    base = await serve(app)
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    destination = tmp_path / "m.gguf"
    partial = destination.with_suffix(destination.suffix + download.PARTIAL)
    partial.write_bytes(body[: len(body) // 2])

    seen: list[download.Progress] = []
    async with download.client() as http:
        await fetch(http, f"{base}/model.gguf", destination, sha(body), seen.append)
    assert destination.read_bytes() == body
    # It counted from where it left off, not from zero.
    assert seen[0].received_bytes > len(body) // 2


async def test_a_host_that_ignores_range_starts_again(
    serve: Serve,
    tmp_path: Path,
    file_server: tuple[FastAPI, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Appending to a host that ignored the range would write the same bytes twice."""
    app, body = file_server
    base = await serve(app)
    monkeypatch.setattr(download, "API_HOSTS", frozenset({"127.0.0.1"}))
    destination = tmp_path / "m.gguf"
    partial = destination.with_suffix(destination.suffix + download.PARTIAL)
    partial.write_bytes(body[:100])

    async with download.client() as http:
        await fetch(http, f"{base}/model.gguf", destination, sha(body))
    assert destination.read_bytes() == body


# --- what is already on disk ----------------------------------------------------------


def test_it_prefers_a_model_that_is_already_downloaded(tmp_path: Path) -> None:
    """Recommending a larger model over one already here costs an hour of waiting,
    and the rig cannot review while it waits."""
    models = tmp_path / "models"
    models.mkdir()
    small = catalog.by_name("gpt-oss-20b")
    (models / small.filename).write_bytes(b"weights")
    assert catalog.recommended_review_model(96, models).name == "gpt-oss-20b"


def test_with_nothing_downloaded_it_picks_by_memory(tmp_path: Path) -> None:
    assert catalog.recommended_review_model(96, tmp_path).name == "Muse-Glimmer-30B"


def test_a_downloaded_model_that_does_not_fit_is_not_chosen(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    large = catalog.by_name("gpt-oss-120b")
    (models / large.filename).write_bytes(b"weights")
    assert catalog.recommended_review_model(11, models).name == "gemma-4-12b-qat"


def test_a_half_finished_download_does_not_count(tmp_path: Path) -> None:
    """A `.part` file is not a model."""
    models = tmp_path / "models"
    models.mkdir()
    small = catalog.by_name("gpt-oss-20b")
    (models / f"{small.filename}.part").write_bytes(b"partial")
    assert catalog.downloaded(small, models) is False


def test_the_default_pair_comes_from_two_families(tmp_path: Path) -> None:
    """A model that checks its own findings has judged nothing, so the reviewer and the
    one that checks it must never be the same weights."""
    reviewer = catalog.recommended_review_model(200, tmp_path)
    checker = catalog.recommended_adversary_model(200, tmp_path)
    assert reviewer.name == "Muse-Glimmer-30B"
    assert checker is not None
    assert checker.name == "gemma-4-31b-qat"
    assert checker.repo.split("/")[0] != reviewer.repo.split("/")[0]


def test_a_machine_that_holds_one_model_is_offered_no_second_one(tmp_path: Path) -> None:
    """Two large models never sit in memory together, but they do swap, and a machine
    with room for one cannot swap at all."""
    assert catalog.recommended_adversary_model(12, tmp_path) is None


def test_a_model_on_disk_counts_as_here(tmp_path: Path) -> None:
    gemma = catalog.by_name("gemma-4-12b-qat")
    assert catalog.downloaded(gemma, tmp_path) is False
    (tmp_path / gemma.filename).write_bytes(b"weights")
    assert catalog.downloaded(gemma, tmp_path) is True

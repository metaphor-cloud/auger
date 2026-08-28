from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response

from auger.llm import DownloadError, Progress, download

Serve = Callable[[object], Awaitable[str]]
WEIGHTS = b"weights" * 4096


def file_server(body: bytes, status: int = 200) -> FastAPI:
    app = FastAPI()

    @app.get("/model.gguf")
    async def model() -> Response:
        return Response(content=body, status_code=status, media_type="application/octet-stream")

    return app


async def test_it_writes_the_file_and_reports_progress(serve: Serve, tmp_path: Path) -> None:
    base = await serve(file_server(WEIGHTS))
    seen: list[Progress] = []
    async with httpx.AsyncClient() as client:
        path = await download(
            client, f"{base}/model.gguf", tmp_path / "m.gguf", on_progress=seen.append
        )
    assert path.read_bytes() == WEIGHTS
    assert seen[-1].received_bytes == len(WEIGHTS)
    assert seen[-1].fraction == 1.0


async def test_it_checks_the_checksum(serve: Serve, tmp_path: Path) -> None:
    base = await serve(file_server(WEIGHTS))
    digest = hashlib.sha256(WEIGHTS).hexdigest()
    async with httpx.AsyncClient() as client:
        path = await download(client, f"{base}/model.gguf", tmp_path / "m.gguf", sha256=digest)
    assert path.exists()


async def test_a_wrong_checksum_leaves_no_file(serve: Serve, tmp_path: Path) -> None:
    """A file that looks complete but is wrong would fail later, inside a review."""
    base = await serve(file_server(WEIGHTS))
    async with httpx.AsyncClient() as client:
        with pytest.raises(DownloadError, match="checksum"):
            await download(client, f"{base}/model.gguf", tmp_path / "m.gguf", sha256="00" * 32)
    assert list(tmp_path.iterdir()) == []


async def test_a_failed_transfer_leaves_no_file(serve: Serve, tmp_path: Path) -> None:
    base = await serve(file_server(b"", status=500))
    async with httpx.AsyncClient() as client:
        with pytest.raises(DownloadError):
            await download(client, f"{base}/model.gguf", tmp_path / "m.gguf")
    assert list(tmp_path.iterdir()) == []


async def test_a_file_already_there_is_not_fetched_again(serve: Serve, tmp_path: Path) -> None:
    base = await serve(file_server(WEIGHTS))
    destination = tmp_path / "m.gguf"
    destination.write_bytes(b"already here")
    async with httpx.AsyncClient() as client:
        await download(client, f"{base}/model.gguf", destination)
    assert destination.read_bytes() == b"already here"

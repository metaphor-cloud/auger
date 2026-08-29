"""Searching for a model to run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from auger.llm.sources import HuggingFace, SourceError, source_for

Serve = Callable[[object], Awaitable[str]]


def test_a_provider_is_chosen_by_name() -> None:
    assert source_for("huggingface").name == "huggingface"
    assert source_for("").name == "huggingface"
    with pytest.raises(SourceError):
        source_for("somewhere-else")


def test_a_shard_is_not_offered() -> None:
    """A model split across files needs a merge step the rig does not have."""
    from auger.llm.sources import SHARDED, SUFFIX

    assert SUFFIX == ".gguf"
    assert SHARDED == "-00001-of-"


async def test_it_reads_the_repositories_a_search_returns(serve: Serve) -> None:
    app = FastAPI()
    seen: dict[str, Any] = {}

    @app.get("/api/models")
    # `filter` is the query parameter Hugging Face uses, so the name is theirs. FastAPI
    # reads it from the signature, and an alias would not be the parameter it sends.
    async def models(
        search: str = "",
        filter: str = "",  # noqa: A002
    ) -> list[dict[str, Any]]:
        seen["search"], seen["filter"] = search, filter
        return [
            {
                "modelId": "acme/thing-GGUF",
                "downloads": 42,
                "likes": 3,
                "gated": False,
                "lastModified": "2026-08-01T00:00:00.000Z",
            },
            {"id": "other/one-GGUF", "downloads": 7, "gated": True},
        ]

    base = await serve(app)
    hf = HuggingFace()
    import auger.llm.sources as sources

    sources.HUGGINGFACE = base
    try:
        async with httpx.AsyncClient() as http:
            found = await hf.search(http, "thing")
    finally:
        sources.HUGGINGFACE = "https://huggingface.co"

    assert seen["filter"] == "gguf"
    assert [one.id for one in found] == ["acme/thing-GGUF", "other/one-GGUF"]
    assert found[0].downloads == 42
    assert found[1].gated is True
    assert found[0].updated == "2026-08-01"


async def test_an_empty_search_asks_for_nothing(serve: Serve) -> None:
    async with httpx.AsyncClient() as http:
        assert await HuggingFace().search(http, "   ") == []


async def test_a_gate_says_what_to_do_about_it(serve: Serve) -> None:
    """A 401 is not a network fault. The way past it is a licence and a token."""
    app = FastAPI()

    @app.get("/api/models/{owner}/{name}/tree/main")
    async def tree(owner: str, name: str) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": "gated"}, status_code=401)

    base = await serve(app)
    import auger.llm.sources as sources

    sources.HUGGINGFACE = base
    try:
        async with httpx.AsyncClient() as http:
            with pytest.raises(SourceError) as raised:
                await HuggingFace().files(http, "google/thing")
    finally:
        sources.HUGGINGFACE = "https://huggingface.co"

    assert "gated" in str(raised.value)
    assert "token" in str(raised.value)

"""Shared test builders."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DEFAULT_REMOTE = "git@github.com:acme/thing.git"


def make_repo(path: Path, remote: str | None = DEFAULT_REMOTE) -> Path:
    """Create a directory that looks like a git checkout."""
    git = path / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    config = "[core]\n\trepositoryformatversion = 0\n"
    if remote:
        config += f'[remote "origin"]\n\turl = {remote}\n\tfetch = +refs/heads/*\n'
    (git / "config").write_text(config, encoding="utf-8")
    return path


class FakeModelServer:
    """An OpenAI-compatible server, so the gateway is tested over real HTTP.

    A mock of the HTTP client would test the mock. This tests the request that the
    gateway actually sends and the response it actually parses.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.models = ["test-model"]
        #: Answer the next N requests with this status instead of 200.
        self.fail_times = 0
        self.fail_status = 503
        self.delay_seconds = 0.0
        self.concurrent = 0
        self.peak_concurrent = 0

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/v1/models")
        async def models() -> dict[str, object]:
            return {"data": [{"id": name} for name in self.models]}

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> JSONResponse:
            body = await self._record(request)
            if (early := self._failure()) is not None:
                return early
            return JSONResponse(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": f"answer:{body['model']}"}}
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
            )

        @app.post("/v1/embeddings")
        async def embeddings(request: Request) -> JSONResponse:
            body = await self._record(request)
            if (early := self._failure()) is not None:
                return early
            rows = [
                {"index": index, "embedding": [float(index), 0.5]}
                for index, _ in enumerate(body["input"])
            ]
            # Out of order on purpose. The gateway must sort by index.
            return JSONResponse({"data": list(reversed(rows))})

        @app.post("/v1/rerank")
        async def rerank(request: Request) -> JSONResponse:
            body = await self._record(request)
            if (early := self._failure()) is not None:
                return early
            results = [
                {"index": index, "relevance_score": 1.0 / (index + 1)}
                for index, _ in enumerate(body["documents"])
            ]
            return JSONResponse({"results": list(reversed(results))})

        return app

    async def _record(self, request: Request) -> dict[str, Any]:
        body = await request.json()
        self.requests.append({"path": request.url.path, **body})
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
        finally:
            self.concurrent -= 1
        return body  # type: ignore[no-any-return]

    def _failure(self) -> JSONResponse | None:
        if self.fail_times <= 0:
            return None
        self.fail_times -= 1
        return JSONResponse({"error": "busy"}, status_code=self.fail_status)

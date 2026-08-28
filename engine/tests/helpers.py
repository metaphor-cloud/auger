"""Shared test builders."""

from __future__ import annotations

import asyncio
import subprocess
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
        #: What the assistant answers. None gives the default echo.
        self.reply: str | None = None
        #: Length of the vector returned per input. 0 turns embedding off.
        self.dimension = 8
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
            content = self.reply if self.reply is not None else f"answer:{body['model']}"
            return JSONResponse(
                {
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
            )

        @app.post("/v1/embeddings")
        async def embeddings(request: Request) -> JSONResponse:
            body = await self._record(request)
            if (early := self._failure()) is not None:
                return early
            rows = [
                {"index": index, "embedding": [float(index)] + [0.5] * (self.dimension - 1)}
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


def git_init(path: Path, remote: str | None = DEFAULT_REMOTE) -> Path:
    """A real git repository, so the git reader is tested against real git."""
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }

    def run(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, env=env)

    run("init", "--quiet", "--initial-branch=main")
    if remote:
        run("remote", "add", "origin", remote)
    return path


def git_commit(path: Path, files: dict[str, str], message: str) -> str:
    """Write files, commit them, and return the new commit sha."""
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--quiet", "-m", message],
        check=True,
        capture_output=True,
        env=env,
    )
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()

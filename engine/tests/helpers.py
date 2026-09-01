"""Shared test builders."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

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
        #: Answers to give in order, before `reply` takes over again. For a test that
        #: needs a bad answer followed by a good one.
        self.replies: list[str] = []
        #: Length of the vector returned per input. 0 turns embedding off.
        self.dimension = 8
        #: Tool calls the assistant asks for, until `tool_call_rounds` runs out.
        self.tool_calls: list[dict[str, Any]] = []
        self.tool_call_rounds = 99
        #: How many tool-calling turns have been served, for `{round}` substitution.
        self.rounds_served = 0
        self.concurrent = 0
        self.peak_concurrent = 0
        #: What a real server sends when a reply stopped at `max_tokens`.
        self.finish_reason = "stop"

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
            if self.replies:
                content = self.replies.pop(0)
            else:
                content = self.reply if self.reply is not None else f"answer:{body['model']}"
            message: dict[str, Any] = {"role": "assistant", "content": content}
            if self.tool_calls and self.tool_call_rounds > 0:
                self.tool_call_rounds -= 1
                # `{round}` anywhere in a call becomes the turn number, so a test can
                # ask for a different call each turn rather than the same one again.
                turn = str(self.rounds_served)
                self.rounds_served += 1
                message["tool_calls"] = json.loads(
                    json.dumps(self.tool_calls).replace("{round}", turn)
                )
                message["content"] = ""
            return JSONResponse(
                {
                    "choices": [{"message": message, "finish_reason": self.finish_reason}],
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


PULL_DIFF = """\
diff --git a/reader.py b/reader.py
--- a/reader.py
+++ b/reader.py
@@ -1,2 +1,3 @@
 def read(path):
-    return path
+    handle = open(path)
+    return handle.read()
"""


class FakeGitHub:
    """The GitHub endpoints the rig uses, over real HTTP."""

    def __init__(self) -> None:
        self.login = "ru"
        self.pulls: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.diff = PULL_DIFF
        self.rate_limited = False
        self.tokens: list[str] = []

    def add_pull(
        self,
        number: int = 7,
        assignees: list[str] | None = None,
        reviewers: list[str] | None = None,
        draft: bool = False,
        sha: str = "abc123",
    ) -> None:
        self.pulls.append(
            {
                "number": number,
                "title": f"Change {number}",
                "html_url": f"https://github.com/acme/thing/pull/{number}",
                "user": {"login": "someone"},
                "head": {"sha": sha},
                "base": {"ref": "main"},
                "draft": draft,
                "assignees": [{"login": name} for name in assignees or []],
                "requested_reviewers": [{"login": name} for name in reviewers or []],
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )

    def app(self) -> FastAPI:
        app = FastAPI()

        def guard(request: Request) -> JSONResponse | None:
            self.tokens.append(request.headers.get("authorization", ""))
            if self.rate_limited:
                return JSONResponse(
                    {"message": "rate limited"},
                    status_code=403,
                    headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999"},
                )
            return None

        @app.get("/user")
        async def user(request: Request) -> Any:
            return guard(request) or {"login": self.login}

        @app.get("/repos/{owner}/{name}/pulls")
        async def pulls(owner: str, name: str, request: Request) -> Any:
            return guard(request) or self.pulls

        @app.get("/repos/{owner}/{name}/pulls/{number}")
        async def one(owner: str, name: str, number: int, request: Request) -> Any:
            blocked = guard(request)
            if blocked:
                return blocked
            if "diff" in request.headers.get("accept", ""):
                return Response(content=self.diff, media_type="text/plain")
            return next(pull for pull in self.pulls if pull["number"] == number)

        @app.post("/repos/{owner}/{name}/pulls/{number}/reviews")
        async def review(owner: str, name: str, number: int, request: Request) -> Any:
            blocked = guard(request)
            if blocked:
                return blocked
            body = await request.json()
            self.reviews.append({"number": number, **body})
            return {"id": 99, "html_url": f"https://github.com/{owner}/{name}/pull/{number}"}

        return app


class FakeGitLab:
    """The GitLab endpoints the rig uses, over real HTTP."""

    def __init__(self) -> None:
        self.username = "ru"
        self.merge_requests: list[dict[str, Any]] = []
        self.draft_notes: list[str] = []
        self.published = False
        self.tokens: list[str] = []

    def add_merge_request(
        self,
        iid: int = 3,
        assignees: list[str] | None = None,
        reviewers: list[str] | None = None,
        draft: bool = False,
    ) -> None:
        self.merge_requests.append(
            {
                "iid": iid,
                "title": f"Change {iid}",
                "web_url": f"https://gitlab.com/acme/thing/-/merge_requests/{iid}",
                "author": {"username": "someone"},
                "sha": "def456",
                "target_branch": "main",
                "draft": draft,
                "assignees": [{"username": name} for name in assignees or []],
                "reviewers": [{"username": name} for name in reviewers or []],
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/user")
        async def user(request: Request) -> Any:
            self.tokens.append(request.headers.get("private-token", ""))
            return {"username": self.username}

        @app.get("/projects/{project:path}/merge_requests")
        async def merge_requests(project: str) -> Any:
            return self.merge_requests

        @app.get("/projects/{project:path}/merge_requests/{iid}/changes")
        async def changes(project: str, iid: int) -> Any:
            return {
                "changes": [
                    {
                        "new_path": "reader.py",
                        "old_path": "reader.py",
                        "diff": "@@ -1,2 +1,3 @@\n-    return path\n+    handle = open(path)\n",
                    }
                ]
            }

        @app.post("/projects/{project:path}/merge_requests/{iid}/draft_notes")
        async def draft_note(project: str, iid: int, request: Request) -> Any:
            body = await request.json()
            self.draft_notes.append(str(body.get("note", "")))
            return {"id": len(self.draft_notes)}

        @app.post("/projects/{project:path}/merge_requests/{iid}/draft_notes/bulk_publish")
        async def publish(project: str, iid: int) -> Any:
            self.published = True
            return {}

        return app

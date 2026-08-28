"""GitLab.

GitLab has real draft notes: they are written, they wait, and a person publishes them.
That maps onto `draft` mode exactly, so the rig never speaks for the user by accident.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from auger.forge.base import Comment, Forge, ForgeError, PostedReview, PullRequest, Repo
from auger.log import Logger, create_logger


class GitLab:
    kind = "gitlab"

    def __init__(
        self, client: httpx.AsyncClient, api: str, token: str, host: str, log: Logger | None = None
    ) -> None:
        self._client = client
        self._api = api.rstrip("/")
        self._token = token
        self.host = host
        self.log = (log or create_logger("forge")).bind(component="forge", forge=self.kind)

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token}

    @staticmethod
    def _project(repo: Repo) -> str:
        return repo.project or quote(repo.slug, safe="")

    async def _get(self, path: str) -> Any:
        response = await self._client.get(f"{self._api}{path}", headers=self._headers())
        self._check(response, "GET", path)
        return response.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = await self._client.post(f"{self._api}{path}", headers=self._headers(), json=body)
        self._check(response, "POST", path)
        return response.json() if response.content else {}

    def _check(self, response: httpx.Response, method: str, path: str) -> None:
        if response.is_success:
            return
        if response.status_code == 429:
            self.log.warn("forge rate limited", reason="rate_limited", path=path)
            raise ForgeError(f"{self.host} rate limit reached. It resets on its own.")
        self.log.warn(
            "forge request failed",
            reason="forge_error",
            method=method,
            path=path,
            status=response.status_code,
        )
        raise ForgeError(f"{method} {path} returned {response.status_code}")

    async def whoami(self) -> str:
        body = await self._get("/user")
        return str(body.get("username", ""))

    async def pull_requests(self, repo: Repo) -> list[PullRequest]:
        body = await self._get(
            f"/projects/{self._project(repo)}/merge_requests?state=opened&per_page=50"
        )
        return [_to_pull(entry) for entry in body]

    async def diff(self, repo: Repo, number: int) -> str:
        body = await self._get(f"/projects/{self._project(repo)}/merge_requests/{number}/changes")
        parts = []
        for change in body.get("changes", []):
            path = change.get("new_path") or change.get("old_path", "")
            parts.append(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n")
            parts.append(change.get("diff", ""))
        return "".join(parts)

    async def post_review(
        self, repo: Repo, pull: PullRequest, summary: str, comments: list[Comment], submit: bool
    ) -> PostedReview:
        project = self._project(repo)
        base = f"/projects/{project}/merge_requests/{pull.number}"
        written = 0
        for comment in [Comment(path="", line=None, body=summary), *comments]:
            body = {"note": _note(comment)}
            await self._post(f"{base}/draft_notes", body)
            written += 1
        if submit:
            await self._post(f"{base}/draft_notes/bulk_publish", {})
        self.log.info(
            "review posted", repo=repo.slug, pull=pull.number, submitted=submit, comments=written
        )
        return PostedReview(id=str(pull.number), submitted=submit, url=pull.url, comments=written)


def _note(comment: Comment) -> str:
    if not comment.path:
        return comment.body
    where = f"`{comment.path}:{comment.line}`" if comment.line else f"`{comment.path}`"
    return f"{where}\n\n{comment.body}"


def _to_pull(entry: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(entry["iid"]),
        title=str(entry.get("title", "")),
        author=str((entry.get("author") or {}).get("username", "")),
        url=str(entry.get("web_url", "")),
        head_sha=str(entry.get("sha", "")),
        base_ref=str(entry.get("target_branch", "")),
        draft=bool(entry.get("draft", entry.get("work_in_progress", False))),
        assignees=tuple(user["username"] for user in entry.get("assignees") or []),
        reviewers=tuple(user["username"] for user in entry.get("reviewers") or []),
        updated_at=str(entry.get("updated_at", "")),
    )


def build(
    client: httpx.AsyncClient, api: str, token: str, host: str, log: Logger | None = None
) -> Forge:
    return GitLab(client, api, token, host, log)

"""GitHub.

A review posted without an `event` is a pending review: it appears in the pull request
for the user to read and submit. That is what `draft` mode does, and it is why nothing
the rig writes reaches the wider team until a person agrees.
"""

from __future__ import annotations

from typing import Any

import httpx

from auger.forge.base import Comment, Forge, ForgeError, PostedReview, PullRequest, Repo
from auger.log import Logger, create_logger

ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"


class GitHub:
    kind = "github"

    def __init__(
        self, client: httpx.AsyncClient, api: str, token: str, host: str, log: Logger | None = None
    ) -> None:
        self._client = client
        self._api = api.rstrip("/")
        self._token = token
        self.host = host
        self.log = (log or create_logger("forge")).bind(component="forge", forge=self.kind)

    def _headers(self, accept: str = ACCEPT) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def _get(self, path: str, accept: str = ACCEPT) -> httpx.Response:
        response = await self._client.get(f"{self._api}{path}", headers=self._headers(accept))
        self._check(response, "GET", path)
        return response

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = await self._client.post(f"{self._api}{path}", headers=self._headers(), json=body)
        self._check(response, "POST", path)
        return response.json()

    def _check(self, response: httpx.Response, method: str, path: str) -> None:
        if response.is_success:
            return
        remaining = response.headers.get("x-ratelimit-remaining")
        if response.status_code in (403, 429) and remaining == "0":
            self.log.warn(
                "forge rate limited",
                reason="rate_limited",
                path=path,
                resets_at=response.headers.get("x-ratelimit-reset", ""),
            )
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
        body = (await self._get("/user")).json()
        return str(body.get("login", ""))

    async def pull_requests(self, repo: Repo) -> list[PullRequest]:
        body = (await self._get(f"/repos/{repo.slug}/pulls?state=open&per_page=50")).json()
        return [_to_pull(entry) for entry in body]

    async def diff(self, repo: Repo, number: int) -> str:
        response = await self._get(
            f"/repos/{repo.slug}/pulls/{number}", accept="application/vnd.github.v3.diff"
        )
        return response.text

    async def post_review(
        self, repo: Repo, pull: PullRequest, summary: str, comments: list[Comment], submit: bool
    ) -> PostedReview:
        body: dict[str, Any] = {
            "commit_id": pull.head_sha,
            "body": summary,
            "comments": [
                {"path": comment.path, "line": comment.line, "body": comment.body}
                for comment in comments
                if comment.line
            ],
        }
        if submit:
            # Without an event the review stays pending, and only the user submits it.
            body["event"] = "COMMENT"
        result = await self._post(f"/repos/{repo.slug}/pulls/{pull.number}/reviews", body)
        self.log.info(
            "review posted",
            repo=repo.slug,
            pull=pull.number,
            submitted=submit,
            comments=len(body["comments"]),
        )
        return PostedReview(
            id=str(result.get("id", "")),
            submitted=submit,
            url=str(result.get("html_url", "")),
            comments=len(body["comments"]),
        )


def _to_pull(entry: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(entry["number"]),
        title=str(entry.get("title", "")),
        author=str((entry.get("user") or {}).get("login", "")),
        url=str(entry.get("html_url", "")),
        head_sha=str((entry.get("head") or {}).get("sha", "")),
        base_ref=str((entry.get("base") or {}).get("ref", "")),
        draft=bool(entry.get("draft", False)),
        assignees=tuple(user["login"] for user in entry.get("assignees") or []),
        reviewers=tuple(user["login"] for user in entry.get("requested_reviewers") or []),
        updated_at=str(entry.get("updated_at", "")),
    )


def build(
    client: httpx.AsyncClient, api: str, token: str, host: str, log: Logger | None = None
) -> Forge:
    return GitHub(client, api, token, host, log)

"""The only path from a job to a model.

A job asks for a job class, never for a model. The profile turns that into a backend, and
this gateway holds one semaphore per backend so a continuous batch server stays full and
is never over-committed.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx

from reviewrig.config.schema import Backend, Config, JobClass, Profile, ProfileEntry
from reviewrig.log import Logger, create_logger
from reviewrig.net import Allowlist, EgressRefused, guarded_client

RETRY_DELAYS = (0.5, 2.0, 5.0)


class ModelError(RuntimeError):
    """The model could not answer."""


class MissingBackendError(ModelError):
    """The profile names a backend that the config does not define."""


class HostedRefusedError(ModelError):
    """The backend sends code off the machine and the user has not allowed that."""


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class Completion:
    text: str
    backend: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Usage:
    """What the rig spent, per backend. The UI shows it and an audit reads it."""

    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failures: int = 0


@dataclass
class Resolved:
    """A job class turned into a concrete backend and its limits."""

    name: str
    backend: Backend
    entry: ProfileEntry
    profile: str


class Gateway:
    def __init__(
        self,
        config: Config,
        allowlist: Allowlist,
        log: Logger | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.log = (log or create_logger("llm")).bind(component="llm")
        self._client = client or guarded_client(allowlist, self.log)
        self._limits: dict[str, asyncio.Semaphore] = {}
        self.usage: dict[str, Usage] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        """The guarded client. The supervisor probes through it, so a probe meets the
        same allowlist as a completion."""
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()

    def profile(self, name: str) -> Profile:
        profile = self.config.profile.get(name)
        if profile is None:
            raise MissingBackendError(f"no profile named {name!r}")
        return profile

    def resolve(self, job_class: JobClass, profile_name: str) -> Resolved:
        entry = self.profile(profile_name).entry(job_class)
        backend = self.config.backend.get(entry.backend)
        if backend is None:
            raise MissingBackendError(
                f"profile {profile_name!r} sends {job_class.value} to backend "
                f"{entry.backend!r}, which the config does not define"
            )
        if backend.hosted and not self.config.egress.allow_hosted:
            raise HostedRefusedError(
                f"backend {entry.backend!r} sends your code off this machine. Set "
                "`allow_hosted = true` under [egress] to permit that."
            )
        return Resolved(name=entry.backend, backend=backend, entry=entry, profile=profile_name)

    def _limit(self, name: str, backend: Backend) -> asyncio.Semaphore:
        if name not in self._limits:
            self._limits[name] = asyncio.Semaphore(backend.max_concurrent)
        return self._limits[name]

    def _headers(self, backend: Backend) -> dict[str, str]:
        if not backend.api_key_env:
            return {}
        key = os.environ.get(backend.api_key_env, "")
        if not key:
            self.log.warn(
                "backend key missing",
                reason="no_api_key",
                variable=backend.api_key_env,
            )
            return {}
        return {"Authorization": f"Bearer {key}"}

    async def _post(self, resolved: Resolved, path: str, payload: dict[str, Any]) -> Any:
        url = resolved.backend.url.rstrip("/") + path
        usage = self.usage.setdefault(resolved.name, Usage())
        last: Exception | None = None
        async with self._limit(resolved.name, resolved.backend):
            for attempt, delay in enumerate((*RETRY_DELAYS, None)):
                try:
                    response = await self._client.post(
                        url, json=payload, headers=self._headers(resolved.backend)
                    )
                    response.raise_for_status()
                    usage.requests += 1
                    return response.json()
                except EgressRefused:
                    # The allowlist refused it. A retry cannot help and must not happen.
                    usage.failures += 1
                    raise
                except (httpx.HTTPStatusError, httpx.RequestError) as error:
                    last = error
                    if delay is None or not _worth_retrying(error):
                        break
                    self.log.warn(
                        "model request failed, retrying",
                        reason="model_retry",
                        backend=resolved.name,
                        attempt=attempt + 1,
                        error=error,
                    )
                    await asyncio.sleep(delay)
        usage.failures += 1
        self.log.error(
            "model request failed",
            reason="model_unreachable",
            backend=resolved.name,
            url=url,
            error=last,
        )
        raise ModelError(f"{resolved.name} did not answer: {last}") from last

    async def complete(
        self,
        job_class: JobClass,
        messages: list[Message],
        profile: str = "balanced",
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        resolved = self.resolve(job_class, profile)
        payload: dict[str, Any] = {
            "model": resolved.backend.model,
            "messages": [message.as_dict() for message in messages],
            "max_tokens": resolved.entry.max_tokens,
            "temperature": resolved.entry.temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        body = await self._post(resolved, "/chat/completions", payload)
        return _completion(body, resolved, self.usage[resolved.name])

    async def embed(self, texts: list[str], profile: str = "balanced") -> list[list[float]]:
        if not texts:
            return []
        resolved = self.resolve(JobClass.EMBED, profile)
        body = await self._post(
            resolved, "/embeddings", {"model": resolved.backend.model, "input": texts}
        )
        try:
            rows = sorted(body["data"], key=lambda row: row.get("index", 0))
            return [list(row["embedding"]) for row in rows]
        except (KeyError, TypeError) as error:
            raise ModelError(f"{resolved.name} returned no embedding") from error

    async def rerank(
        self, query: str, documents: list[str], profile: str = "balanced"
    ) -> list[float]:
        """Return one score per document, in the order the documents were given."""
        if not documents:
            return []
        resolved = self.resolve(JobClass.RERANK, profile)
        body = await self._post(
            resolved,
            "/rerank",
            {"model": resolved.backend.model, "query": query, "documents": documents},
        )
        try:
            scores = [0.0] * len(documents)
            for row in body["results"]:
                scores[int(row["index"])] = float(row["relevance_score"])
            return scores
        except (KeyError, TypeError, IndexError, ValueError) as error:
            raise ModelError(f"{resolved.name} returned no score") from error


def _worth_retrying(error: Exception) -> bool:
    """A connection loss or an overloaded server is worth another try. A 400 is not."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 429, 500, 502, 503, 504}
    return isinstance(error, httpx.RequestError)


def _completion(body: Any, resolved: Resolved, usage: Usage) -> Completion:
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as error:
        raise ModelError(f"{resolved.name} returned no message") from error
    counts = body.get("usage") or {}
    prompt = int(counts.get("prompt_tokens", 0))
    completion = int(counts.get("completion_tokens", 0))
    usage.prompt_tokens += prompt
    usage.completion_tokens += completion
    return Completion(
        text=text,
        backend=resolved.name,
        model=resolved.backend.model,
        prompt_tokens=prompt,
        completion_tokens=completion,
    )

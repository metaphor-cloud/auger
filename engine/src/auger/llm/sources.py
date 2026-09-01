"""Where models come from.

The rig ships a short list of models it recommends, and that list is the expectation.
This is the layer underneath it: a place to search for anything else, and a shape that
another provider can be added to later without touching the rest.

Hugging Face is the only provider today. A second one implements `Source` and appears
beside it; nothing above this file knows which one answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx

from auger.log import Logger, create_logger

HUGGINGFACE = "https://huggingface.co"
#: How many results one search returns. A long list is not a better answer.
LIMIT = 20
#: Only single file models. A model split across shards needs a merge step the rig
#: does not have, so it is not offered.
SUFFIX = ".gguf"
SHARDED = "-00001-of-"
#: What a conversion for the second engine is called, near enough. There is no tag for
#: the format, and the people who publish one put the engine's name in the repository
#: name, so this is the only filter there is. Whether a repository really holds weights
#: that engine can read is decided when it is fetched, by reading the tree.
SHARD_HINT = "colibri"


class SourceError(RuntimeError):
    """The provider could not be reached, or refused."""


@dataclass(frozen=True)
class Repository:
    """One model repository, as a search result."""

    source: str
    id: str
    downloads: int
    likes: int
    #: True when the publisher requires a licence acceptance before a download.
    gated: bool
    updated: str = ""

    @property
    def url(self) -> str:
        return f"{HUGGINGFACE}/{self.id}"


@dataclass(frozen=True)
class File:
    """One file inside a repository, which is what actually gets fetched."""

    name: str
    size_bytes: int

    @property
    def gigabytes(self) -> float:
        return self.size_bytes / 1_000_000_000


class Source(Protocol):
    """A place models can be searched for and fetched from."""

    name: str

    async def search(self, http: httpx.AsyncClient, query: str) -> list[Repository]: ...

    async def search_shards(self, http: httpx.AsyncClient, query: str) -> list[Repository]: ...

    async def files(self, http: httpx.AsyncClient, repo: str) -> list[File]: ...


class HuggingFace:
    """Hugging Face, searched for models the rig can actually run.

    The filter is `gguf`, because that is what `llama-server` loads. A repository of
    safetensors is not something this rig can use, so it is not shown.
    """

    name = "huggingface"

    def __init__(self, token: str | None = None, log: Logger | None = None) -> None:
        self.token = token
        self.log = (log or create_logger("llm")).bind(component="sources")

    def _headers(self, url: str) -> dict[str, str]:
        from auger.net.download import auth_for

        return auth_for(url, self.token)

    async def search(self, http: httpx.AsyncClient, query: str) -> list[Repository]:
        wanted = query.strip()
        if not wanted:
            return []
        return await self._found(
            http,
            f"{HUGGINGFACE}/api/models?search={quote(wanted)}&filter=gguf"
            f"&sort=downloads&direction=-1&limit={LIMIT}",
        )

    async def search_shards(self, http: httpx.AsyncClient, query: str) -> list[Repository]:
        """Repositories that look like a conversion for the second engine.

        The `gguf` filter is exactly wrong here: these are safetensors, which is why the
        other engine cannot read them. There is no tag for the format, so the search is
        by name and the real check happens when the tree is read at fetch time.
        """
        wanted = query.strip()
        term = wanted if SHARD_HINT in wanted.lower() else f"{wanted} {SHARD_HINT}".strip()
        return await self._found(
            http,
            f"{HUGGINGFACE}/api/models?search={quote(term)}"
            f"&sort=downloads&direction=-1&limit={LIMIT}",
        )

    async def _found(self, http: httpx.AsyncClient, url: str) -> list[Repository]:
        try:
            response = await http.get(url, headers=self._headers(url))
            response.raise_for_status()
            found = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise SourceError(f"could not search Hugging Face: {error}") from error
        return [
            Repository(
                source=self.name,
                id=str(one.get("modelId") or one.get("id") or ""),
                downloads=int(one.get("downloads") or 0),
                likes=int(one.get("likes") or 0),
                gated=bool(one.get("gated")),
                updated=str(one.get("lastModified") or "")[:10],
            )
            for one in found
            if one.get("modelId") or one.get("id")
        ]

    async def files(self, http: httpx.AsyncClient, repo: str) -> list[File]:
        """The weights inside one repository, largest last.

        Size comes from the tree, which is the same place the checksum comes from, so
        what the window shows is what the download will check.
        """
        url = f"{HUGGINGFACE}/api/models/{repo}/tree/main"
        try:
            response = await http.get(url, headers=self._headers(url))
            response.raise_for_status()
            entries = response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                raise SourceError(
                    f"{repo} is gated. Accept its licence at {HUGGINGFACE}/{repo}, then "
                    f"set a Hugging Face token in the variable your config names."
                ) from error
            raise SourceError(f"could not read {repo}: {error}") from error
        except (httpx.HTTPError, ValueError) as error:
            raise SourceError(f"could not read {repo}: {error}") from error

        files: list[File] = []
        for entry in entries:
            name = str(entry.get("path", ""))
            if not name.endswith(SUFFIX) or SHARDED in name:
                continue
            size = int((entry.get("lfs") or {}).get("size") or entry.get("size") or 0)
            files.append(File(name=name, size_bytes=size))
        return sorted(files, key=lambda one: one.size_bytes)


def source_for(name: str, token: str | None = None, log: Logger | None = None) -> Source:
    """The provider by name. One today, and the shape for the next."""
    if name in ("", HuggingFace.name):
        return HuggingFace(token, log)
    raise SourceError(f"no model source named {name!r}")

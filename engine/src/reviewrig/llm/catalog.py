"""The models the rig can fetch for itself.

A first run should not begin with a shopping list. The rig knows a small set of models
that work, knows how much memory each one needs, and picks the one that fits the machine
it is on.

Every entry names a public repository and a file. The checksum and the size come from
that repository's API at fetch time, never from this list, so nothing here can go stale
in a way that matters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from reviewrig.config.schema import JobClass
from reviewrig.log import Logger, create_logger

HUGGINGFACE = "https://huggingface.co"
#: macOS gives a model roughly three quarters of unified memory, and a review needs room
#: for its context on top of the weights.
USABLE_FRACTION = 0.70


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class Choice:
    """One model the rig can fetch."""

    name: str
    job_class: JobClass
    repo: str
    filename: str
    #: Roughly what it needs in memory, weights plus a working context.
    memory_gb: float
    description: str

    @property
    def url(self) -> str:
        return f"{HUGGINGFACE}/{self.repo}/resolve/main/{self.filename}"

    @property
    def tree_url(self) -> str:
        return f"{HUGGINGFACE}/api/models/{self.repo}/tree/main"


@dataclass(frozen=True)
class Resolved:
    choice: Choice
    url: str
    sha256: str
    size_bytes: int


#: Review models, largest first. The rig picks the first one that fits.
REVIEW_MODELS: tuple[Choice, ...] = (
    Choice(
        name="gpt-oss-120b",
        job_class=JobClass.REVIEW,
        repo="ggml-org/gpt-oss-120b-GGUF",
        filename="gpt-oss-120b-MXFP4.gguf",
        memory_gb=80.0,
        description="The strongest reviewer. 63 GB of weights.",
    ),
    Choice(
        name="gpt-oss-20b",
        job_class=JobClass.REVIEW,
        repo="ggml-org/gpt-oss-20b-GGUF",
        filename="gpt-oss-20b-MXFP4.gguf",
        memory_gb=18.0,
        description="Fits a laptop. 12 GB of weights.",
    ),
)

EMBED_MODEL = Choice(
    name="Qwen3-Embedding-0.6B",
    job_class=JobClass.EMBED,
    repo="Qwen/Qwen3-Embedding-0.6B-GGUF",
    filename="Qwen3-Embedding-0.6B-Q8_0.gguf",
    memory_gb=2.0,
    description="Turns code into vectors, so retrieval finds code that was renamed.",
)

CATALOG: tuple[Choice, ...] = (*REVIEW_MODELS, EMBED_MODEL)


def total_memory_bytes() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return 0


def usable_memory_gb() -> float:
    return total_memory_bytes() / 1e9 * USABLE_FRACTION


def recommended_review_model(memory_gb: float | None = None) -> Choice:
    """The largest review model this machine can hold. Never returns nothing."""
    available = usable_memory_gb() if memory_gb is None else memory_gb
    for choice in REVIEW_MODELS:
        if choice.memory_gb <= available:
            return choice
    return REVIEW_MODELS[-1]


def by_name(name: str) -> Choice:
    for choice in CATALOG:
        if choice.name == name:
            return choice
    raise CatalogError(f"no model named {name!r}")


async def resolve(http: httpx.AsyncClient, choice: Choice, log: Logger | None = None) -> Resolved:
    """Ask the repository for the file's checksum and size.

    The checksum comes from the API host, which the download path matches exactly. That
    is what makes it safe for a delivery host to be matched by suffix.
    """
    log = (log or create_logger("llm")).bind(component="catalog")
    try:
        response = await http.get(choice.tree_url)
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogError(f"could not read {choice.repo}: {error}") from error

    for entry in entries:
        if entry.get("path") != choice.filename:
            continue
        oid = str((entry.get("lfs") or {}).get("oid", ""))
        if not oid:
            raise CatalogError(f"{choice.filename} publishes no checksum")
        log.info("model resolved", model=choice.name, size=entry.get("size"))
        return Resolved(
            choice=choice, url=choice.url, sha256=oid, size_bytes=int(entry.get("size", 0))
        )
    raise CatalogError(f"{choice.repo} has no file named {choice.filename}")

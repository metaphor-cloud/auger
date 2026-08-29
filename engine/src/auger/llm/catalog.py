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
from pathlib import Path

import httpx

from auger.config.schema import JobClass
from auger.log import Logger, create_logger

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

#: Models that argue with the reviewer. A second opinion is only worth having when it
#: comes from somewhere else, so these are chosen to be from other families than the
#: reviewer above, not to be the strongest thing that fits.
ADVERSARY_MODELS: tuple[Choice, ...] = (
    Choice(
        name="Qwen3-Coder-30B",
        job_class=JobClass.VERIFY,
        repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        filename="Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
        memory_gb=24.0,
        description="Trained on code, from a different family than the reviewer. 18 GB.",
    ),
    Choice(
        name="gemma-3-12b-qat",
        job_class=JobClass.VERIFY,
        # Google's own repository is gated behind a licence acceptance, and the rig
        # carries no Hugging Face token, so it fetches the same weights from a mirror.
        repo="lmstudio-community/gemma-3-12B-it-qat-GGUF",
        filename="gemma-3-12B-it-QAT-Q4_0.gguf",
        memory_gb=11.0,
        description="Quantisation aware trained, so it holds up at four bits. 8 GB.",
    ),
    Choice(
        name="Qwen3-8B",
        job_class=JobClass.VERIFY,
        repo="Qwen/Qwen3-8B-GGUF",
        filename="Qwen3-8B-Q4_K_M.gguf",
        memory_gb=8.0,
        description="The small one. Fits beside a large reviewer. 5 GB.",
    ),
)

#: Embedding models, most capable first. The rig picks the first one that fits, and the
#: user can choose the other in the Models view.
EMBED_MODELS: tuple[Choice, ...] = (
    Choice(
        name="nomic-embed-code",
        job_class=JobClass.EMBED,
        repo="nomic-ai/nomic-embed-code-GGUF",
        filename="nomic-embed-code.Q4_K_M.gguf",
        memory_gb=6.0,
        description="Built for code. 4.4 GB, and slower to index.",
    ),
    Choice(
        name="Qwen3-Embedding-0.6B",
        job_class=JobClass.EMBED,
        repo="Qwen/Qwen3-Embedding-0.6B-GGUF",
        filename="Qwen3-Embedding-0.6B-Q8_0.gguf",
        memory_gb=2.0,
        description="General purpose. 0.64 GB, and quick to index.",
    ),
)

#: Available, and not fetched by default. Measured on this repository over 25 symbols
#: with references computed from the syntax tree, reranking made retrieval markedly
#: worse: recall at 12 fell from 0.686 to 0.373 and precision at 5 from 0.448 to 0.144.
#: Rank fusion of exact name search with code embeddings is already a stronger signal
#: than a small cross encoder's judgement, and the reranker replaces the good ordering
#: with its own. It stays here because a better reranker may earn its place later.
RERANK_MODEL = Choice(
    name="Qwen3-Reranker-0.6B",
    job_class=JobClass.RERANK,
    repo="DevQuasar/Qwen.Qwen3-Reranker-0.6B-GGUF",
    filename="Qwen.Qwen3-Reranker-0.6B.Q8_0.gguf",
    memory_gb=1.5,
    description="Reorders the retrieved code. Measured worse than not reordering.",
)

#: The small embedder, for a machine that cannot spare the memory for the code one.
SMALL_EMBED_MODEL = EMBED_MODELS[-1]

CATALOG: tuple[Choice, ...] = (
    *REVIEW_MODELS,
    *ADVERSARY_MODELS,
    *EMBED_MODELS,
    RERANK_MODEL,
)


def total_memory_bytes() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return 0


def usable_memory_gb() -> float:
    return total_memory_bytes() / 1e9 * USABLE_FRACTION


def _largest_that_fits(choices: tuple[Choice, ...], available: float) -> Choice:
    """Never returns nothing. A machine with no fitting model still needs a way forward."""
    for choice in choices:
        if choice.memory_gb <= available:
            return choice
    return choices[-1]


def downloaded(choice: Choice, models_dir: Path | None) -> bool:
    return models_dir is not None and (models_dir / choice.filename).is_file()


def _already_here(
    choices: tuple[Choice, ...], available: float, models_dir: Path | None
) -> Choice | None:
    """The best model that fits and is already on disk."""
    for choice in choices:
        if choice.memory_gb <= available and downloaded(choice, models_dir):
            return choice
    return None


def recommended_review_model(
    memory_gb: float | None = None, models_dir: Path | None = None
) -> Choice:
    """The best model this machine can hold, preferring one it already has.

    Weights are tens of gigabytes. Recommending a larger model over one that is already
    downloaded means an hour of waiting and a rig that cannot review in the meantime.
    """
    available = usable_memory_gb() if memory_gb is None else memory_gb
    return _already_here(REVIEW_MODELS, available, models_dir) or _largest_that_fits(
        REVIEW_MODELS, available
    )


def recommended_embed_model(
    memory_gb: float | None = None, models_dir: Path | None = None
) -> Choice:
    """The code embedder where the machine can hold it.

    Measured on this repository, it raised recall at 12 from 0.613 to 0.686 and brought
    the first correct file from rank 2 to rank 1. It costs 4.4 GB against 0.64 GB, and
    about six times the indexing time, which is paid once and then only per changed file.
    """
    available = usable_memory_gb() if memory_gb is None else memory_gb
    return _already_here(EMBED_MODELS, available, models_dir) or _largest_that_fits(
        EMBED_MODELS, available
    )


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

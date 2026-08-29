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
    #: Its publisher requires a licence acceptance, so fetching it needs a token.
    gated: bool = False
    #: Where to get it when the publisher's gate is shut. A licence acceptance happens
    #: in a browser, once, and a rig that cannot fetch a model it recommends is worse
    #: than one that says where the bytes came from.
    #:
    #: This is a community build of the same weights, not a copy of the publisher's
    #: file, and the window says which one it used.
    open_repo: str = ""
    open_filename: str = ""

    def source(self, token: str | None = None) -> tuple[str, str]:
        """The repository and file to fetch, and whether a token changes it."""
        if self.gated and not token and self.open_repo:
            return self.open_repo, self.open_filename or self.filename
        return self.repo, self.filename

    def url_for(self, token: str | None = None) -> str:
        repo, filename = self.source(token)
        return f"{HUGGINGFACE}/{repo}/resolve/main/{filename}"

    def tree_for(self, token: str | None = None) -> str:
        repo, _ = self.source(token)
        return f"{HUGGINGFACE}/api/models/{repo}/tree/main"

    @property
    def url(self) -> str:
        return self.url_for(None)

    @property
    def tree_url(self) -> str:
        return self.tree_for(None)


@dataclass(frozen=True)
class Resolved:
    choice: Choice
    url: str
    sha256: str
    size_bytes: int
    #: Where the bytes are actually coming from, which is not always the publisher.
    repo: str = ""
    filename: str = ""


#: What the rig recommends, largest first, and it picks the first that fits.
#:
#: Three families, so a reviewer and an adversary can always come from different ones.
#: A second opinion from the same family is barely a second opinion.
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
        name="Muse-Glimmer-30B",
        job_class=JobClass.REVIEW,
        repo="meta-models/Muse-Glimmer-30B-GGUF",
        filename="Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
        memory_gb=22.0,
        description="Meta's, and Apache licensed. 17 GB.",
    ),
    Choice(
        name="gpt-oss-20b",
        job_class=JobClass.REVIEW,
        repo="ggml-org/gpt-oss-20b-GGUF",
        filename="gpt-oss-20b-MXFP4.gguf",
        memory_gb=18.0,
        description="Fits a laptop. 12 GB of weights.",
    ),
    Choice(
        name="gemma-3-12b-qat",
        job_class=JobClass.REVIEW,
        repo="google/gemma-3-12b-it-qat-q4_0-gguf",
        filename="gemma-3-12b-it-q4_0.gguf",
        memory_gb=11.0,
        description="Quantisation aware trained, so it holds up at four bits. 8 GB.",
        # Google require a licence acceptance, which happens in a browser, once. With
        # a token the file comes from them. Without one it comes from the community
        # build below, and the window says which one it used.
        gated=True,
        open_repo="lmstudio-community/gemma-3-12B-it-qat-GGUF",
        open_filename="gemma-3-12B-it-QAT-Q4_0.gguf",
    ),
)

#: Models that argue with the reviewer. The same three families: what matters is that
#: the one judging is not the one that wrote the finding.
ADVERSARY_MODELS: tuple[Choice, ...] = tuple(
    Choice(
        name=one.name,
        job_class=JobClass.VERIFY,
        repo=one.repo,
        filename=one.filename,
        memory_gb=one.memory_gb,
        description=one.description,
        gated=one.gated,
        open_repo=one.open_repo,
        open_filename=one.open_filename,
    )
    for one in REVIEW_MODELS
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


def _open(choices: tuple[Choice, ...]) -> tuple[Choice, ...]:
    """The ones anybody can fetch.

    A gated model needs a licence acceptance and a token, so recommending one to a
    machine that has neither is a first run that ends in a 401. It stays in the list
    for a user to choose on purpose.
    """
    return tuple(choice for choice in choices if not choice.gated) or choices


def _largest_that_fits(choices: tuple[Choice, ...], available: float) -> Choice:
    """Never returns nothing. A machine with no fitting model still needs a way forward."""
    open_ones = _open(choices)
    for choice in open_ones:
        if choice.memory_gb <= available:
            return choice
    return open_ones[-1]


def downloaded(choice: Choice, models_dir: Path | None, token: str | None = None) -> bool:
    """Whether the file this would fetch is already here.

    A gated model fetched without a token lands under the community build's file name,
    so asking for the publisher's name would say no to a model that is on the disk.
    """
    if models_dir is None:
        return False
    # Every name it could be under. Setting a token later must not make a model that
    # is already on the disk look missing.
    names = {choice.filename, choice.open_filename, choice.source(token)[1]}
    return any(name and (models_dir / name).is_file() for name in names)


def _already_here(
    choices: tuple[Choice, ...], available: float, models_dir: Path | None
) -> Choice | None:
    """The best model that fits and is already on disk."""
    for choice in _open(choices):
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


async def resolve(
    http: httpx.AsyncClient,
    choice: Choice,
    log: Logger | None = None,
    token: str | None = None,
) -> Resolved:
    """Ask the repository for the file's checksum and size.

    The checksum comes from the API host, which the download path matches exactly. That
    is what makes it safe for a delivery host to be matched by suffix.
    """
    log = (log or create_logger("llm")).bind(component="catalog")
    from auger.net.download import auth_for

    try:
        tree = choice.tree_for(token)
        response = await http.get(tree, headers=auth_for(tree, token))
        response.raise_for_status()
        entries = response.json()
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (401, 403):
            # A gate is not a network fault, and the way past it is a licence and a
            # token, so the message says that rather than the status code.
            repo, _ = choice.source(token)
            raise CatalogError(
                f"{repo} is gated. Accept its licence at "
                f"https://huggingface.co/{repo}, then set a Hugging Face token "
                f"in the variable your config names."
            ) from error
        raise CatalogError(f"could not read {choice.repo}: {error}") from error
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogError(f"could not read {choice.repo}: {error}") from error

    repo, filename = choice.source(token)
    for entry in entries:
        if entry.get("path") != filename:
            continue
        oid = str((entry.get("lfs") or {}).get("oid", ""))
        if not oid:
            raise CatalogError(f"{filename} publishes no checksum")
        log.info(
            "model resolved",
            model=choice.name,
            size=entry.get("size"),
            # Which source answered. A model that came from a community build rather
            # than from its publisher is a fact worth having in the log.
            repo=repo,
            publisher=repo == choice.repo,
        )
        return Resolved(
            choice=choice,
            url=choice.url_for(token),
            sha256=oid,
            size_bytes=int(entry.get("size", 0)),
            repo=repo,
            filename=filename,
        )
    raise CatalogError(f"{repo} has no file named {filename}")

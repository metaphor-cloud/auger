"""Build the code around a diff.

A diff on its own hides the two things a reviewer needs most: what the changed code is
part of, and who calls it. Three searches answer that, and none of them is enough alone.

- By overlap: which indexed chunks the changed lines sit in.
- By keyword: which chunks name the changed symbols. That finds a caller in any
  language, and it works when nothing was renamed.
- By meaning: which chunks are close to the change. That finds the code that matters
  when the change introduced a name that appears nowhere else.

The reranker then puts the survivors in order, and a budget keeps the prompt from
growing without limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reviewrig.config.schema import JobClass
from reviewrig.llm import Gateway, ModelError
from reviewrig.log import Logger, create_logger
from reviewrig.store import Store
from reviewrig.store.index import Hit, chunks_in_file, search_text, search_vectors

HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
TARGET = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
#: How many characters of related code go into one prompt.
DEFAULT_BUDGET = 24_000
#: How many chunks reach the reranker.
CANDIDATES = 40
#: A name shorter than this matches too much to mean anything.
MIN_NAME = 4
#: How many changed symbol names go into one keyword query.
MAX_NAMES = 40
#: A chunk that a grammar named is worth more than a window of lines from a file that
#: no grammar reads. A licence and a lock file are both windows of lines.
LINE_CHUNK_WEIGHT = 0.35
#: The constant in reciprocal rank fusion. 60 is the value the method was published
#: with, and it flattens the difference between the first few ranks.
RRF_K = 60


@dataclass(frozen=True)
class Change:
    path: str
    ranges: list[tuple[int, int]]


@dataclass
class ReviewContext:
    hits: list[Hit] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    reranked: bool = False

    def as_text(self, budget: int = DEFAULT_BUDGET) -> str:
        """The related code, largest relevance first, until the budget runs out."""
        parts: list[str] = []
        used = 0
        for hit in self.hits:
            block = f"--- {hit.label}\n{hit.text}\n"
            if used + len(block) > budget:
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


def changed_ranges(diff: str) -> list[Change]:
    """Which lines of which files the diff touches, on the new side."""
    changes: dict[str, list[tuple[int, int]]] = {}
    path = ""
    for line in diff.splitlines():
        target = TARGET.match(line)
        if target:
            path = target.group(1).strip()
            if path == "/dev/null":
                path = ""
            continue
        match = HUNK.match(line)
        if match and path:
            start = int(match.group(2))
            length = int(match.group(3) or 1)
            changes.setdefault(path, []).append((start, start + max(length, 1) - 1))
    return [Change(path=path, ranges=ranges) for path, ranges in changes.items()]


def overlapping(store: Store, repository: str, changes: list[Change]) -> list[Hit]:
    """The chunks that the changed lines sit inside."""
    found: list[Hit] = []
    for change in changes:
        for hit in chunks_in_file(store, repository, change.path):
            if any(hit.start_line <= end and start <= hit.end_line for start, end in change.ranges):
                found.append(Hit(**{**hit.__dict__, "score": 1.0}))
    return found


def symbol_names(hits: list[Hit]) -> list[str]:
    """The leaf name of every symbol the change sits in, without duplicates."""
    names: list[str] = []
    for hit in hits:
        leaf = hit.symbol.split(" part ")[0].split(".")[-1].strip()
        if leaf and leaf not in names:
            names.append(leaf)
    return names


def searchable(names: list[str]) -> list[str]:
    """The names worth searching for.

    A short name matches too much to mean anything. Sorting by length is not a measure of
    specificity: the longest names in a large change are test functions, and each one
    matches only itself.
    """
    return [name for name in names if len(name) >= MIN_NAME][:MAX_NAMES]


def callers(store: Store, repository: str, names: list[str], limit: int = CANDIDATES) -> list[Hit]:
    """The chunks that name the changed symbols.

    One query, not one per name. BM25 ranks a chunk that mentions several of the changed
    names above one that mentions a single common word, which is what separates a caller
    from a licence file.
    """
    wanted = searchable(names)
    if not wanted:
        return []
    return [_weighted(hit) for hit in search_text(store, " ".join(wanted), repository, limit)]


def _weighted(hit: Hit) -> Hit:
    """Lower the score of a chunk that no grammar named."""
    if hit.symbol:
        return hit
    return Hit(**{**hit.__dict__, "score": hit.score * LINE_CHUNK_WEIGHT})


def merge(groups: list[list[Hit]], exclude: set[int] = frozenset()) -> list[Hit]:  # type: ignore[assignment]
    """One list, combined by rank rather than by score.

    The scores of the two searches are not comparable. Keyword search reports BM25,
    where a good match is a small negative number, and vector search reports a distance,
    where a good match is near zero. Any formula that maps both to "bigger is better"
    still puts them on different scales, and the larger scale wins every time regardless
    of quality. Measured on this repository, mixing them by score cut recall almost in
    half against keyword search alone.

    Reciprocal rank fusion avoids the question. Each list contributes 1/(k + rank), so
    only the order within a list matters, and a chunk that both searches like beats one
    that only a single search likes.
    """
    combined: dict[int, float] = {}
    best: dict[int, Hit] = {}
    for group in groups:
        for rank, hit in enumerate(group, start=1):
            if hit.chunk_id in exclude:
                continue
            combined[hit.chunk_id] = combined.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            best.setdefault(hit.chunk_id, hit)
    return [
        Hit(**{**best[chunk_id].__dict__, "score": score})
        for chunk_id, score in sorted(combined.items(), key=lambda item: -item[1])
    ]


def rerank_query(names: list[str]) -> str:
    """What to ask the reranker.

    A reranker is a cross encoder trained on a question and a document. A unified diff
    is not a question, and asking with one measured far worse than not reranking at all.
    The question this retrieval actually asks is which code uses the changed symbols, so
    that is what it asks.
    """
    wanted = searchable(names)
    if not wanted:
        return "code related to this change"
    return "code that calls or uses " + ", ".join(wanted[:8])


async def context_for_diff(
    store: Store,
    gateway: Gateway | None,
    repository: str,
    diff: str,
    profile: str = "balanced",
    limit: int = 12,
    log: Logger | None = None,
) -> ReviewContext:
    """Gather the code around a diff. Never raises: a failure returns less context."""
    log = (log or create_logger("context")).bind(component="retrieve")
    changes = changed_ranges(diff)
    if not changes:
        return ReviewContext()

    inside = overlapping(store, repository, changes)
    names = symbol_names(inside)
    # The changed chunks are already in the diff. Related code is what is missing.
    seen = {hit.chunk_id for hit in inside}
    groups = [callers(store, repository, names)]

    if gateway is not None and gateway.available(JobClass.EMBED, profile):
        try:
            vectors = await gateway.embed([diff[:8000]], profile=profile)
        except ModelError as error:
            log.warn("semantic search skipped", reason="embed_failed", error=error)
            vectors = []
        if vectors:
            groups.append(
                [
                    _weighted(hit)
                    for hit in search_vectors(store, vectors[0], repository, limit=CANDIDATES)
                ]
            )

    candidates = merge(groups, exclude=seen)[:CANDIDATES]
    context = ReviewContext(hits=candidates[:limit], symbols=names)

    reranks = gateway is not None and gateway.available(JobClass.RERANK, profile)
    if reranks and gateway is not None and len(candidates) > limit:
        try:
            scores = await gateway.rerank(
                rerank_query(names), [hit.text for hit in candidates], profile
            )
        except ModelError as error:
            log.warn("rerank skipped", reason="rerank_failed", error=error)
            scores = []
        if len(scores) == len(candidates):
            ordered = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
            context.hits = [
                Hit(**{**hit.__dict__, "score": score}) for hit, score in ordered[:limit]
            ]
            context.reranked = True
    return context

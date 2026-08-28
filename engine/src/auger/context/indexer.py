"""Keep the index in step with the repository.

Only a file whose blob sha moved is read, parsed, and embedded. A re-index after one
commit therefore costs one file, not a whole tree, which is what makes a continuous rig
affordable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from auger.config.schema import JobClass
from auger.context.chunker import Chunk, chunk_file
from auger.context.languages import indexable
from auger.llm import Gateway, ModelError
from auger.log import Logger, create_logger
from auger.store import Store
from auger.store.index import (
    chunk_count,
    ensure_vectors,
    forget_files,
    replace_file,
    store_vectors,
    stored_blobs,
)
from auger.watch import git

#: Texts sent to the embedding model in one request.
BATCH = 32


@dataclass
class IndexOutcome:
    files_seen: int = 0
    files_changed: int = 0
    files_removed: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    duration_ms: int = 0
    error: str | None = None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def changed_files(store: Store, repository: Path) -> tuple[dict[str, str], list[str], list[str]]:
    """Return the current blobs, the paths to re-read, and the paths to forget."""
    current = git.tracked_blobs(repository)
    known = stored_blobs(store, repository)
    changed = [path for path, sha in current.items() if known.get(path) != sha]
    removed = [path for path in known if path not in current]
    return current, changed, removed


async def reindex(
    store: Store,
    gateway: Gateway | None,
    repository: Path,
    profile: str = "balanced",
    log: Logger | None = None,
) -> IndexOutcome:
    """Bring one repository's index up to date. Never raises."""
    log = (log or create_logger("context")).bind(component="indexer")
    started = time.monotonic()
    outcome = IndexOutcome()
    try:
        current, changed, removed = changed_files(store, repository)
    except git.GitError as error:
        outcome.error = str(error)
        log.warn("index skipped", reason="git_failed", repo=str(repository), error=error)
        return outcome

    outcome.files_seen = len(current)
    outcome.files_removed = forget_files(store, repository, removed)

    pending_ids: list[int] = []
    pending_texts: list[str] = []
    for path in changed:
        full = repository / path
        try:
            size = full.stat().st_size
        except OSError:
            continue
        if not indexable(path, size):
            # Keep the sha, so a file the rig will never index is not re-read every cycle.
            replace_file(store, repository, path, current[path], [])
            continue
        source = _read(full)
        if source is None:
            replace_file(store, repository, path, current[path], [])
            continue
        chunks: list[Chunk] = chunk_file(path, source, log)
        ids = replace_file(store, repository, path, current[path], chunks)
        outcome.files_changed += 1
        outcome.chunks_written += len(chunks)
        pending_ids += ids
        pending_texts += [chunk.text for chunk in chunks]

    if gateway is not None and pending_texts and gateway.available(JobClass.EMBED, profile):
        outcome.chunks_embedded = await _embed(
            store, gateway, pending_ids, pending_texts, profile, log
        )

    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "index updated",
        repo=str(repository),
        files_seen=outcome.files_seen,
        files_changed=outcome.files_changed,
        files_removed=outcome.files_removed,
        chunks=chunk_count(store, repository),
        embedded=outcome.chunks_embedded,
        ms=outcome.duration_ms,
    )
    return outcome


async def _embed(
    store: Store,
    gateway: Gateway,
    ids: list[int],
    texts: list[str],
    profile: str,
    log: Logger,
) -> int:
    written = 0
    for start in range(0, len(texts), BATCH):
        batch_ids = ids[start : start + BATCH]
        batch_texts = texts[start : start + BATCH]
        try:
            vectors = await gateway.embed(batch_texts, profile=profile)
        except ModelError as error:
            # Keyword search still works without vectors, so a failure here degrades
            # retrieval instead of failing the index.
            log.warn("embedding skipped", reason="embed_failed", error=error)
            return written
        if not vectors:
            return written
        if not ensure_vectors(store, len(vectors[0])):
            log.warn("vectors unavailable", reason="no_vector_table")
            return written
        written += store_vectors(store, batch_ids, vectors)
    return written

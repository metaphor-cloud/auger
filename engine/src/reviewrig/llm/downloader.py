"""Fetch model weights.

A weights file is tens of gigabytes, so the download reports progress, resumes nothing,
and writes to a partial file that only becomes the real one when the whole transfer and
the checksum both pass. A half written file that looks complete would fail later, in a
review, with a message that says nothing useful.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from reviewrig.log import Logger, create_logger

CHUNK = 1 << 20
PARTIAL_SUFFIX = ".part"


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Progress:
    name: str
    received_bytes: int
    total_bytes: int

    @property
    def fraction(self) -> float:
        return self.received_bytes / self.total_bytes if self.total_bytes else 0.0


async def download(
    client: httpx.AsyncClient,
    url: str,
    destination: Path,
    sha256: str = "",
    on_progress: Callable[[Progress], None] | None = None,
    log: Logger | None = None,
) -> Path:
    """Fetch `url` into `destination`. Returns the path, and skips a file already there."""
    log = (log or create_logger("llm")).bind(component="downloader")
    if destination.exists():
        log.info("model already present", path=str(destination))
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + PARTIAL_SUFFIX)
    digest = hashlib.sha256()
    received = 0
    log.info("model download started", url=url, path=str(destination))
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes(CHUNK):
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(Progress(destination.name, received, total))
    except (httpx.HTTPError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"could not fetch {url}: {error}") from error

    if sha256 and digest.hexdigest() != sha256:
        partial.unlink(missing_ok=True)
        raise DownloadError(
            f"{destination.name} does not match its checksum. Expected {sha256}, "
            f"got {digest.hexdigest()}"
        )
    partial.replace(destination)
    log.info("model download finished", path=str(destination), bytes=received)
    return destination

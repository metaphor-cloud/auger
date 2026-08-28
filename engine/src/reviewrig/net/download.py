"""Fetching the things the rig needs to run: a model runtime, and model weights.

This is the one path where a host is matched by suffix rather than exactly, and there is
a reason. A file is served by a content delivery host whose name varies by region and
changes over time, so an exact list would break. Every byte is checked against a sha256
that came from the API host, and that host **is** matched exactly. A delivery host
therefore cannot substitute content without being caught.

Nothing here carries the user's code. It only brings bytes in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from reviewrig.log import Logger, create_logger

#: Exactly matched. These publish the checksums.
API_HOSTS: frozenset[str] = frozenset({"api.github.com", "github.com", "huggingface.co", "hf.co"})
#: Matched by suffix. These deliver bytes, and a checksum decides whether to keep them.
DELIVERY_SUFFIXES: tuple[str, ...] = (".hf.co", ".huggingface.co", ".githubusercontent.com")
MAX_REDIRECTS = 6
REDIRECTS = frozenset({301, 302, 303, 307, 308})
PARTIAL_CONTENT = 206
CHUNK = 1 << 20
PARTIAL = ".part"


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


def allowed(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    return host in API_HOSTS or host.endswith(DELIVERY_SUFFIXES)


def client(timeout: float = 60.0) -> httpx.AsyncClient:
    """A client for downloads only. It never carries repository content."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


async def fetch(
    http: httpx.AsyncClient,
    url: str,
    destination: Path,
    sha256: str,
    on_progress: Callable[[Progress], None] | None = None,
    log: Logger | None = None,
) -> Path:
    """Download `url` to `destination` and verify it. A file already there is kept.

    A checksum is required. Without one there is nothing to check the delivery host
    against, and a file that looks complete but is wrong fails later, inside a review,
    with a message that says nothing useful.
    """
    log = (log or create_logger("download")).bind(component="download")
    if destination.exists():
        log.info("already present", path=str(destination))
        return destination
    if not sha256:
        raise DownloadError(f"{destination.name} has no published checksum")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + PARTIAL)
    digest, already = _resume_point(partial)
    log.info("download started", name=destination.name, url=safe_url(url), resume_from=already)
    try:
        received, digest = await _stream(
            http, url, partial, digest, destination.name, already, on_progress, log
        )
    except DownloadError:
        partial.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"could not fetch {destination.name}: {error}") from error

    if digest.hexdigest() != sha256.removeprefix("sha256:"):
        partial.unlink(missing_ok=True)
        log.error(
            "download refused",
            reason="checksum_mismatch",
            name=destination.name,
            expected=sha256,
        )
        raise DownloadError(f"{destination.name} does not match its published checksum")
    partial.replace(destination)
    log.info("download finished", name=destination.name, bytes=received)
    return destination


def _resume_point(partial: Path) -> tuple[hashlib._Hash, int]:
    """Hash whatever was already written, so a resumed file still checks out.

    Weights are tens of gigabytes. Starting again after a dropped connection at ninety
    per cent is the difference between a setup that finishes and one that never does.
    """
    digest = hashlib.sha256()
    if not partial.exists():
        return digest, 0
    already = 0
    with partial.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            already += len(chunk)
    return digest, already


async def _stream(
    http: httpx.AsyncClient,
    url: str,
    partial: Path,
    digest: hashlib._Hash,
    name: str,
    already: int,
    on_progress: Callable[[Progress], None] | None,
    log: Logger,
) -> tuple[int, hashlib._Hash]:
    """Follow the redirects on the GET itself, checking every hop.

    A separate HEAD would be one more round trip, and not every delivery host answers
    one, so the check happens on the request that matters.

    Returns the byte count and the digest that was actually used. A restart replaces the
    digest, and the caller must verify against that one, not against the one it passed in.
    """
    current = url
    received = already
    for _ in range(MAX_REDIRECTS):
        if not allowed(current):
            log.warn("download refused", reason="not_allowlisted", url=safe_url(current))
            raise DownloadError(f"{urlsplit(current).hostname} is not a download host")
        headers = {"Range": f"bytes={already}-"} if already else {}
        async with http.stream("GET", current, headers=headers) as response:
            if response.status_code in REDIRECTS:
                location = response.headers.get("location", "")
                if not location:
                    raise DownloadError(f"{name}: a redirect with no destination")
                current = str(httpx.URL(current).join(location))
                continue
            response.raise_for_status()
            resumed = response.status_code == PARTIAL_CONTENT and already > 0
            if already and not resumed:
                # The host ignored the range. Start again rather than write a file that
                # holds the same bytes twice.
                log.info("download restarted", name=name, reason="no_range_support")
                digest, already, received = hashlib.sha256(), 0, 0
            total = int(response.headers.get("content-length", 0)) + already
            with partial.open("ab" if resumed else "wb") as handle:
                async for chunk in response.aiter_bytes(CHUNK):
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(Progress(name, received, total))
            return received, digest
    raise DownloadError(f"{name}: too many redirects")


def safe_url(url: str) -> str:
    """A signed delivery URL carries a credential in its query. Log the path only."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{parts.path}"

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
from typing import Any
from urllib.parse import urlsplit

import httpx

from auger.log import Logger, create_logger

#: Exactly matched. These publish the checksums.
API_HOSTS: frozenset[str] = frozenset({"api.github.com", "github.com", "huggingface.co", "hf.co"})
#: Matched by suffix. These deliver bytes, and a checksum decides whether to keep them.
DELIVERY_SUFFIXES: tuple[str, ...] = (".hf.co", ".huggingface.co", ".githubusercontent.com")
#: Where a Hugging Face token may be sent, and nowhere else. A token is a credential
#: for one service, and a redirect must not be able to carry it to another.
TOKEN_HOSTS: frozenset[str] = frozenset({"huggingface.co", "hf.co"})
TOKEN_SUFFIXES: tuple[str, ...] = (".hf.co", ".huggingface.co")
MAX_REDIRECTS = 6
REDIRECTS = frozenset({301, 302, 303, 307, 308})
PARTIAL_CONTENT = 206
CHUNK = 1 << 20
PARTIAL = ".part"


class DownloadError(RuntimeError):
    pass


#: The two checksums a model repository publishes, and what they are worth.
#:
#: A large file is kept out of line and carries a sha256. A small one is kept in the git
#: tree and carries only the git object hash of its contents. Both come from the API
#: host, which this path matches exactly.
#:
#: The weaker of the two is not the only thing standing behind a small file: a file with
#: no sha256 is one the API host serves itself rather than handing to a delivery host, so
#: the exact host match already covers it and the hash is a second check on top. A file
#: with neither is not fetched at all.
SHA256 = "sha256"
GIT_BLOB = "git-blob"


@dataclass(frozen=True)
class Digest:
    algorithm: str
    value: str
    #: A git object hash covers a header carrying the length, so that hash needs the
    #: size the API published. A sha256 does not.
    size: int = 0

    @classmethod
    def sha256(cls, value: str) -> Digest:
        return cls(SHA256, value.removeprefix("sha256:").strip().lower())

    @classmethod
    def git_blob(cls, value: str, size: int) -> Digest:
        return cls(GIT_BLOB, value.strip().lower(), size)

    @classmethod
    def published(cls, entry: dict[str, Any]) -> Digest:
        """The best checksum one entry of a repository tree publishes."""
        lfs = entry.get("lfs")
        oid = str((lfs or {}).get("oid", "")) if isinstance(lfs, dict) else ""
        if oid:
            return cls.sha256(oid)
        size = entry.get("size") or 0
        return cls.git_blob(str(entry.get("oid", "")), int(size))

    @property
    def empty(self) -> bool:
        return not self.value

    def start(self) -> Any:
        if self.algorithm == GIT_BLOB:
            hasher = hashlib.sha1(usedforsecurity=False)
            hasher.update(b"blob %d\0" % self.size)
            return hasher
        return hashlib.sha256()

    def matches(self, hasher: Any) -> bool:
        return bool(hasher.hexdigest() == self.value)


@dataclass(frozen=True)
class Progress:
    name: str
    received_bytes: int
    total_bytes: int

    @property
    def fraction(self) -> float:
        return self.received_bytes / self.total_bytes if self.total_bytes else 0.0


def carries_token(url: str) -> bool:
    """Whether this host is one the token belongs to."""
    host = (urlsplit(url).hostname or "").lower()
    return host in TOKEN_HOSTS or host.endswith(TOKEN_SUFFIXES)


def auth_for(url: str, token: str | None) -> dict[str, str]:
    """The authorization header for this host, if it is one the token belongs to."""
    if not token or not carries_token(url):
        return {}
    return {"Authorization": f"Bearer {token}"}


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
    checksum: Digest | str,
    on_progress: Callable[[Progress], None] | None = None,
    log: Logger | None = None,
    token: str | None = None,
) -> Path:
    """Download `url` to `destination` and verify it. A file already there is kept.

    A checksum is required. Without one there is nothing to check the delivery host
    against, and a file that looks complete but is wrong fails later, inside a review,
    with a message that says nothing useful.

    A plain string is read as a sha256, which is what a release asset publishes.
    """
    log = (log or create_logger("download")).bind(component="download")
    wanted = Digest.sha256(checksum) if isinstance(checksum, str) else checksum
    if destination.exists():
        log.info("already present", path=str(destination))
        return destination
    if wanted.empty:
        raise DownloadError(f"{destination.name} has no published checksum")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + PARTIAL)
    digest, already = _resume_point(partial, wanted)
    log.info("download started", name=destination.name, url=safe_url(url), resume_from=already)
    try:
        received, digest = await _stream(
            http, url, partial, digest, destination.name, already, on_progress, log, token, wanted
        )
    except DownloadError:
        partial.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"could not fetch {destination.name}: {error}") from error

    if not wanted.matches(digest):
        partial.unlink(missing_ok=True)
        log.error(
            "download refused",
            reason="checksum_mismatch",
            name=destination.name,
            algorithm=wanted.algorithm,
            expected=wanted.value,
        )
        raise DownloadError(f"{destination.name} does not match its published checksum")
    partial.replace(destination)
    log.info("download finished", name=destination.name, bytes=received)
    return destination


def _resume_point(partial: Path, wanted: Digest) -> tuple[Any, int]:
    """Hash whatever was already written, so a resumed file still checks out.

    Weights are tens of gigabytes. Starting again after a dropped connection at ninety
    per cent is the difference between a setup that finishes and one that never does,
    and a paused download is a dropped connection the user asked for.
    """
    digest = wanted.start()
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
    digest: Any,
    name: str,
    already: int,
    on_progress: Callable[[Progress], None] | None,
    log: Logger,
    token: str | None = None,
    wanted: Digest | None = None,
) -> tuple[int, Any]:
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
        # The token goes to the host it belongs to and to no other, so a redirect
        # cannot carry a credential somewhere it was never meant for.
        headers.update(auth_for(current, token))
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
                fresh = wanted.start() if wanted is not None else hashlib.sha256()
                digest, already, received = fresh, 0, 0
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

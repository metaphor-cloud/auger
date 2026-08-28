"""The model runtime the rig brings with it.

The point of the rig is that you point it at your code and it works. Asking the user to
install a model server first breaks that, so the rig fetches one: a `llama.cpp` release
build for this platform, into its own directory.

A server the user already has still wins. This is the path for a machine that has none.
"""

from __future__ import annotations

import platform
import stat
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from reviewrig.log import Logger, create_logger
from reviewrig.net.download import DownloadError, Progress, fetch
from reviewrig.sandbox.which import find

RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
#: How many releases to read while looking for one that carries this platform's build.
#: The newest tagged release sometimes carries no binary at all.
RELEASE_PAGE = 15
SERVER = "llama-server"


class RuntimeInstallError(RuntimeError):
    """The runtime could not be found or installed."""


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    sha256: str
    size_bytes: int
    asset: str


def asset_name() -> str | None:
    """The release asset for this machine, or None when there is no build for it."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
    if system == "linux":
        return "ubuntu-arm64" if machine in ("arm64", "aarch64") else "ubuntu-x64"
    return None


def runtime_dir(home: Path) -> Path:
    return home / "runtime"


def installed(home: Path) -> Path | None:
    """The newest runtime the rig installed, if any."""
    root = runtime_dir(home)
    if not root.is_dir():
        return None
    found = sorted(root.glob(f"*/**/{SERVER}"), reverse=True)
    return next((path for path in found if path.is_file()), None)


def resolve(home: Path) -> Path | None:
    """A server the user already has, or the one the rig installed."""
    from_path = find(SERVER)
    if from_path:
        return Path(from_path)
    return installed(home)


async def latest_release(http: httpx.AsyncClient, log: Logger | None = None) -> Release:
    """The newest release that carries a build for this platform."""
    log = (log or create_logger("llm")).bind(component="runtime")
    wanted = asset_name()
    if wanted is None:
        raise RuntimeInstallError(
            f"no llama.cpp build for {platform.system()} {platform.machine()}"
        )
    try:
        response = await http.get(f"{RELEASES}?per_page={RELEASE_PAGE}")
        response.raise_for_status()
        releases = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeInstallError(f"could not read the llama.cpp releases: {error}") from error

    for release in releases:
        for item in release.get("assets", []):
            name = str(item.get("name", ""))
            if wanted in name and name.endswith((".tar.gz", ".zip")):
                digest = str(item.get("digest") or "")
                if not digest:
                    continue  # No checksum, no download.
                log.info("runtime release found", tag=release.get("tag_name"), asset=name)
                return Release(
                    tag=str(release.get("tag_name", "")),
                    url=str(item.get("browser_download_url", "")),
                    sha256=digest,
                    size_bytes=int(item.get("size", 0)),
                    asset=name,
                )
    raise RuntimeInstallError(
        f"no recent llama.cpp release carries a {wanted} build with a checksum"
    )


def _extract(archive: Path, into: Path) -> None:
    """Unpack, refusing any member that would land outside the target directory."""
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # `filter="data"` refuses absolute paths, parent traversal, links, and devices.
        tar.extractall(into, filter="data")


async def install(
    http: httpx.AsyncClient,
    home: Path,
    on_progress: Callable[[Progress], None] | None = None,
    log: Logger | None = None,
) -> Path:
    """Fetch and unpack a runtime. Returns the path to `llama-server`."""
    log = (log or create_logger("llm")).bind(component="runtime")
    release = await latest_release(http, log)
    root = runtime_dir(home) / release.tag
    server = next(iter(sorted(root.glob(f"**/{SERVER}"))), None)
    if server is not None and server.is_file():
        return server

    archive = runtime_dir(home) / release.asset
    try:
        await fetch(http, release.url, archive, release.sha256, on_progress, log)
        _extract(archive, root)
    except (DownloadError, tarfile.TarError, OSError) as error:
        raise RuntimeInstallError(f"could not install the model runtime: {error}") from error
    finally:
        archive.unlink(missing_ok=True)

    server = next(iter(sorted(root.glob(f"**/{SERVER}"))), None)
    if server is None:
        raise RuntimeInstallError(f"{release.asset} held no {SERVER}")
    for binary in root.glob("**/*"):
        if binary.is_file() and not binary.suffix:
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log.info("runtime installed", tag=release.tag, path=str(server))
    return server

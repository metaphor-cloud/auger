"""The second engine, for models that do not fit.

The engine the rig brings by default holds the whole model in memory. That puts a hard
ceiling on a laptop: a 30B dense model and nothing larger. This one streams the experts
of a sparse model from disk and keeps only the dense layers resident, so the same machine
can run a model an order of magnitude larger at the same memory cost, slower per token
but with far more in it.

It is optional, and it stays uninstalled until the user asks. Two things have to be said
before they do, because both are found out too late otherwise:

- It needs Python 3 on the machine. Its launcher is a Python script.
- It answers chat only. There is no embeddings endpoint and no rerank endpoint, so the
  first engine keeps those two job classes whatever else changes.

What it does answer is more than its own reference claims: the gateway it ships takes a
JSON schema response format and turns it into a grammar, streams usage, and handles tool
calls. That is exactly what a review needs, so a review can be served by it.
"""

from __future__ import annotations

import platform
import stat
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from auger.downloads import Item, Job, Manager
from auger.log import Logger, create_logger
from auger.net.download import Digest
from auger.sandbox.which import find

RELEASES = "https://api.github.com/repos/JustVugg/colibri/releases"
#: How many releases to read while looking for one that carries this platform's build.
RELEASE_PAGE = 10
#: The launcher, which is a Python script, and the engine it starts.
LAUNCHER = "coli"
ENGINE = "colibri"
#: What the rig calls this engine in a config file.
NAME = "coli"


class ColiError(RuntimeError):
    """The engine could not be installed, or cannot be used here."""


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    sha256: str
    size_bytes: int
    asset: str


@dataclass(frozen=True)
class Readiness:
    """Whether this engine can run here, and what is missing if not."""

    #: The platform has a published build.
    supported: bool
    #: Python 3 was found. Without it the launcher cannot start at all.
    python: str
    installed: str
    problems: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return self.supported and bool(self.python)


def asset_name() -> str | None:
    """The release asset for this machine, or None when there is no build for it."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    return None


def runtime_dir(home: Path) -> Path:
    return home / "runtime" / NAME


def installed(home: Path) -> Path | None:
    """The newest build of this engine the rig installed, if any."""
    root = runtime_dir(home)
    if not root.is_dir():
        return None
    found = sorted(root.glob(f"*/{LAUNCHER}"), reverse=True)
    return next((path for path in found if path.is_file()), None)


def python() -> str:
    """The interpreter that can run the launcher, or "" when there is none.

    A graphical application has a narrow PATH, so the usual places are tried too.
    """
    found = find("python3")
    if found:
        return found
    for candidate in ("/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if Path(candidate).is_file():
            return candidate
    return ""


def readiness(home: Path) -> Readiness:
    """What stands between the user and this engine, before they commit to a download."""
    asset = asset_name()
    interpreter = python()
    here = installed(home)
    problems: list[str] = []
    if asset is None:
        problems.append(
            f"there is no build of this engine for {platform.system()} {platform.machine()}"
        )
    if not interpreter:
        problems.append(
            "this engine's launcher is a Python 3 script and no python3 was found on "
            "this machine. Install Python 3, then check again."
        )
    return Readiness(
        supported=asset is not None,
        python=interpreter,
        installed=str(here) if here else "",
        problems=tuple(problems),
    )


async def latest_release(http: httpx.AsyncClient, log: Logger | None = None) -> Release:
    """The newest release that carries a build for this platform."""
    log = (log or create_logger("llm")).bind(component="coli")
    wanted = asset_name()
    if wanted is None:
        raise ColiError(f"no build of this engine for {platform.system()} {platform.machine()}")
    try:
        response = await http.get(f"{RELEASES}?per_page={RELEASE_PAGE}")
        response.raise_for_status()
        releases = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ColiError(f"could not read the releases: {error}") from error

    for release in releases:
        for item in release.get("assets", []):
            name = str(item.get("name", ""))
            if wanted not in name or not name.endswith((".tar.gz", ".tgz")):
                continue
            digest = str(item.get("digest") or "")
            if not digest:
                continue  # No checksum, no download.
            log.info("engine release found", tag=release.get("tag_name"), asset=name)
            return Release(
                tag=str(release.get("tag_name", "")),
                url=str(item.get("browser_download_url", "")),
                sha256=digest,
                size_bytes=int(item.get("size", 0)),
                asset=name,
            )
    raise ColiError(f"no recent release carries a {wanted} build with a checksum")


def _extract(archive: Path, into: Path) -> None:
    """Unpack, refusing any member that would land outside the target directory."""
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # `filter="data"` refuses absolute paths, parent traversal, links, and devices.
        tar.extractall(into, filter="data")


async def install(
    http: httpx.AsyncClient,
    home: Path,
    downloads: Manager,
    on_progress: Callable[[Job], None] | None = None,
    log: Logger | None = None,
) -> Path:
    """Fetch and unpack this engine. Returns the path to its launcher."""
    log = (log or create_logger("llm")).bind(component="coli")
    ready = readiness(home)
    if ready.problems:
        raise ColiError(ready.problems[0])
    release = await latest_release(http, log)
    root = runtime_dir(home) / release.tag
    launcher = root / LAUNCHER
    if launcher.is_file():
        return launcher

    archive = runtime_dir(home) / release.asset
    job = downloads.submit(
        f"{NAME} {release.tag}",
        "runtime",
        runtime_dir(home),
        [Item(release.asset, release.url, Digest.sha256(release.sha256), release.size_bytes)],
        watcher=on_progress,
    )
    finished = await downloads.wait(job.id)
    if finished is None or finished.state != "done":
        reason = (finished.error if finished else "") or f"the download was {job.state}"
        raise ColiError(f"could not install this engine: {reason}")
    try:
        _extract(archive, root)
    except (tarfile.TarError, OSError) as error:
        raise ColiError(f"could not unpack this engine: {error}") from error
    finally:
        archive.unlink(missing_ok=True)

    if not launcher.is_file():
        raise ColiError(f"{release.asset} held no {LAUNCHER}")
    # The archive is flat and its members carry no suffix: the launcher, the engine, and
    # one native binary per model family. All of them have to be runnable.
    for member in root.glob("*"):
        if member.is_file() and not member.suffix:
            member.chmod(member.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log.info("engine installed", tag=release.tag, path=str(launcher))
    return launcher

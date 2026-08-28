"""Callers from a real call graph.

Text search finds a name and vector search finds something similar. Neither knows that
one function calls another. CodeGraph does, so where a repository already has an index,
the rig asks it.

It never creates an index. Indexing a repository writes into it, and that is the user's
decision, not the rig's.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from auger.config.schema import CodeGraph as CodeGraphConfig
from auger.log import Logger, create_logger
from auger.sandbox.which import find

INDEX_DIR = ".codegraph"


@dataclass(frozen=True)
class Caller:
    symbol: str
    path: str
    line: int


def indexed(repository: Path) -> bool:
    """Whether this repository already has a CodeGraph index."""
    return (repository / INDEX_DIR).is_dir()


def available(config: CodeGraphConfig, repository: Path) -> str | None:
    """Why CodeGraph cannot answer for this repository, or None when it can."""
    if not config.enabled:
        return "turned off"
    if find(config.command) is None:
        return f"{config.command} is not installed"
    if not indexed(repository):
        return f"no {INDEX_DIR} index in this repository"
    return None


def callers(
    config: CodeGraphConfig, repository: Path, symbol: str, log: Logger | None = None
) -> list[Caller]:
    """The files that call `symbol`. Returns nothing rather than raising."""
    log = (log or create_logger("context")).bind(component="codegraph")
    program = find(config.command)
    if program is None:
        return []
    command = [
        program,
        "callers",
        symbol,
        "-p",
        str(repository),
        "--limit",
        str(config.limit),
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warn("codegraph lookup failed", reason="codegraph_failed", symbol=symbol, error=error)
        return []
    if completed.returncode != 0:
        log.warn(
            "codegraph lookup failed",
            reason="codegraph_error",
            symbol=symbol,
            error=completed.stderr.strip()[:200],
        )
        return []
    return parse(completed.stdout, symbol)


def parse(output: str, symbol: str) -> list[Caller]:
    """Read the JSON. A shape the rig does not know means no callers, not a failure."""
    try:
        body = json.loads(output or "{}")
    except json.JSONDecodeError:
        return []
    entries = body.get("callers") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        return []
    found: list[Caller] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("filePath") or entry.get("path") or "").strip()
        if not path:
            continue
        found.append(Caller(symbol=symbol, path=path, line=int(entry.get("startLine", 1) or 1)))
    return found


def callers_for(
    config: CodeGraphConfig,
    repository: Path,
    symbols: list[str],
    log: Logger | None = None,
) -> list[Caller]:
    """Every caller of every changed symbol, in the order the symbols were given."""
    if available(config, repository) is not None:
        return []
    found: list[Caller] = []
    seen: set[tuple[str, int]] = set()
    for symbol in symbols:
        for caller in callers(config, repository, symbol, log):
            key = (caller.path, caller.line)
            if key not in seen:
                seen.add(key)
                found.append(caller)
    return found

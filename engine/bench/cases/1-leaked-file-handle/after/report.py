"""Write the run report."""

from __future__ import annotations

from pathlib import Path


def write(path: Path, lines: list[str]) -> int:
    handle = path.open("w", encoding="utf-8")
    for line in lines:
        handle.write(line + "\n")
    handle.close()
    return len(lines)

"""Write the run report."""

from __future__ import annotations

from pathlib import Path


def write(path: Path, lines: list[str]) -> int:
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
    return len(lines)

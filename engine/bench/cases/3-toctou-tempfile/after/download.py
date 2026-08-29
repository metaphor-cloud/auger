"""Write a downloaded file into place."""

from __future__ import annotations

from pathlib import Path


def place(target: Path, body: bytes) -> Path:
    """Write the bytes, then move them into place in one step."""
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.parent / f"{target.name}.part"
    if scratch.exists():
        scratch.unlink()
    scratch.write_bytes(body)
    scratch.rename(target)
    return target

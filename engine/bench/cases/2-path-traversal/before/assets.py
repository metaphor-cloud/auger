"""Serve a file from the asset directory."""

from __future__ import annotations

from pathlib import Path


class NotAllowed(Exception):
    pass


def read_asset(root: Path, name: str) -> bytes:
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise NotAllowed(name)
    return target.read_bytes()

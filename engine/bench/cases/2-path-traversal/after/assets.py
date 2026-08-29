"""Serve a file from the asset directory."""

from __future__ import annotations

from pathlib import Path


class NotAllowed(Exception):
    pass


def read_asset(root: Path, name: str) -> bytes:
    if ".." in name:
        raise NotAllowed(name)
    target = (root / name).resolve()
    return target.read_bytes()

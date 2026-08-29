"""Read a table one page at a time."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def pages(rows: list[Any], size: int) -> Iterator[list[Any]]:
    """Every row, in slices of `size`. The last slice may be short."""
    if size <= 0:
        raise ValueError("size must be positive")
    start = 0
    while start < len(rows):
        yield rows[start : start + size]
        start += size


def total_pages(count: int, size: int) -> int:
    return (count + size - 1) // size

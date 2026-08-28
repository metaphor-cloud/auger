"""Turn a file into pieces a model can hold.

A chunk boundary follows a symbol boundary wherever the grammar gives one, because a
retriever that returns half a function returns something a reviewer cannot use. A file
with no grammar falls back to overlapping windows of lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reviewrig.context.repomap import Symbol, map_file
from reviewrig.log import Logger

#: A symbol longer than this is split. It is bigger than a reviewer reads at once.
MAX_LINES = 160
#: Lines repeated between two windows, so a split never hides a boundary.
OVERLAP = 8


@dataclass(frozen=True)
class Chunk:
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    text: str

    @property
    def label(self) -> str:
        where = f"{self.path}:{self.start_line}-{self.end_line}"
        return f"{self.symbol} ({where})" if self.symbol else where


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _windows(
    path: str, lines: list[str], symbol: Symbol | None, start: int, end: int
) -> list[Chunk]:
    chunks: list[Chunk] = []
    cursor = start
    part = 1
    while cursor <= end:
        stop = min(cursor + MAX_LINES - 1, end)
        name = f"{symbol.qualified} part {part}" if symbol else ""
        chunks.append(
            Chunk(
                path=path,
                symbol=name,
                kind=symbol.kind if symbol else "lines",
                start_line=cursor,
                end_line=stop,
                text=_slice(lines, cursor, stop),
            )
        )
        if stop >= end:
            break
        cursor = stop - OVERLAP + 1
        part += 1
    return chunks


def top_level(symbols: list[Symbol]) -> list[Symbol]:
    """Only the outermost symbols. A method is already inside its class."""
    return [symbol for symbol in symbols if "." not in symbol.qualified]


def chunk_file(path: str, source: str, log: Logger | None = None) -> list[Chunk]:
    """Split one file. Returns an empty list for a file with no content."""
    text = source.replace("\r\n", "\n")
    if not text.strip():
        return []
    lines = text.split("\n")
    symbols = top_level(map_file(Path(path), text.encode("utf-8"), log))
    if not symbols:
        return _windows(path, lines, None, 1, len(lines))

    chunks: list[Chunk] = []
    covered = 0
    for symbol in sorted(symbols, key=lambda item: item.start_line):
        if symbol.start_line <= covered:
            continue  # Already inside a symbol that was emitted.
        if symbol.start_line > covered + 1:
            # Imports and module level code between two symbols still matter.
            chunks += _windows(path, lines, None, covered + 1, symbol.start_line - 1)
        if symbol.line_count > MAX_LINES:
            chunks += _windows(path, lines, symbol, symbol.start_line, symbol.end_line)
        else:
            chunks.append(
                Chunk(
                    path=path,
                    symbol=symbol.qualified,
                    kind=symbol.kind,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    text=_slice(lines, symbol.start_line, symbol.end_line),
                )
            )
        covered = max(covered, symbol.end_line)
    if covered < len(lines):
        chunks += _windows(path, lines, None, covered + 1, len(lines))
    return [chunk for chunk in chunks if chunk.text.strip()]

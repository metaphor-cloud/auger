"""Structured JSON logging.

Every line is one JSON object on stdout with `level`, `message`, and optional `data`.
Bind ambient context once with `Logger.bind`, so every line from a job carries its ids.
"""

from __future__ import annotations

import contextlib
import json
import sys
import traceback
from typing import Any, Literal, Self

Level = Literal["debug", "info", "warn", "error"]

_ORDER: dict[Level, int] = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def serialise(value: object) -> object:
    """Make a value JSON-safe. An Error keeps its stack, which a plain str() drops."""
    if isinstance(value, BaseException):
        return {
            "name": type(value).__name__,
            "message": str(value),
            "stack": "".join(traceback.format_exception(value)),
        }
    if isinstance(value, dict):
        return {str(k): serialise(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [serialise(v) for v in value]
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    return repr(value)


class Logger:
    def __init__(self, context: dict[str, Any], min_level: Level = "info") -> None:
        self._context = context
        self._min = _ORDER[min_level]

    def bind(self, **context: Any) -> Self:
        merged = {**self._context, **context}
        clone = type(self)(merged)
        clone._min = self._min
        return clone

    def _emit(self, level: Level, message: str, data: dict[str, Any] | None) -> None:
        if _ORDER[level] < self._min:
            return
        line: dict[str, Any] = {"level": level, "message": message, **self._context}
        if data:
            line["data"] = serialise(data)
        stream = sys.stderr if level in ("warn", "error") else sys.stdout
        # The host holds the read end of these pipes. When the host dies, a write raises.
        # Losing a log line is acceptable. Killing the caller is not: the first line the
        # engine writes after the host dies is the one that reports the host is gone.
        with contextlib.suppress(OSError):
            print(json.dumps(line, default=str), file=stream, flush=True)

    def debug(self, message: str, **data: Any) -> None:
        self._emit("debug", message, data)

    def info(self, message: str, **data: Any) -> None:
        self._emit("info", message, data)

    def warn(self, message: str, **data: Any) -> None:
        self._emit("warn", message, data)

    def error(self, message: str, **data: Any) -> None:
        self._emit("error", message, data)


def create_logger(component: str, min_level: Level = "info") -> Logger:
    return Logger({"component": component}, min_level)

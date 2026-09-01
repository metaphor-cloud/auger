"""Cheap, narrow tools, served from what the engine already has.

A loop is only viable when a turn is cheap. A container start is seconds, so a loop over
`run_command` costs more than the work; these answer out of the index and the working
tree, in memory, in about the time a dictionary lookup takes. Nothing is executed, so
none of the questions a container was there to answer arise.

Narrow beats general for the same reason. `sh -c` makes the model invent a command,
parse whatever it prints, and recover from its own shell errors. `read_file(path, line)`
returns a bounded slice and choosing it is a small decision. That difference matters most
on a weak model, which is the only kind this rig runs.

Every tool here is read only and every one of them is bounded, because the results are
carried in every request after the one that asked for them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auger.config.schema import CodeGraph as CodeGraphConfig
from auger.context import codegraph
from auger.context.repomap import map_file
from auger.log import Logger, create_logger
from auger.store import Store
from auger.store.index import search_text

READ = "read_file"
SEARCH = "search_code"
SYMBOLS = "symbols"
CALLERS = "callers"

#: Lines one read returns. A model that wants more asks again with an offset, which
#: costs one cheap turn; a read that returns a whole file spends the working set.
READ_LINES = 120
#: Characters any one result may be. A minified bundle is one line and megabytes long.
RESULT_CHARS = 6000
#: Chunks one search returns.
SEARCH_LIMIT = 8
#: Characters of each search hit. Enough to see what the code is, not the whole of it.
HIT_CHARS = 700
#: A file larger than this is not source, and reading it is not what the model meant.
MAX_FILE_BYTES = 2_000_000


def _cut(text: str, limit: int = RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[cut after {limit} characters. Ask for a narrower slice.]"


@dataclass(frozen=True)
class Lookup:
    """The engine's own index and working tree, as tools a model can call."""

    store: Store
    repository: Path
    graph: CodeGraphConfig | None = None

    # --- what the model is shown -------------------------------------------------

    def schema(self) -> list[dict[str, Any]]:
        return [
            _function(
                READ,
                "Read a slice of one file in the repository under review. Use it to see "
                "code the diff refers to but does not show.",
                {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the repository root.",
                    },
                    "line": {
                        "type": "integer",
                        "description": f"First line to return. Returns {READ_LINES} "
                        "lines from there. Omit for the start of the file.",
                    },
                },
                ["path"],
            ),
            _function(
                SEARCH,
                "Search the indexed code for a word or a name, and get back the code "
                "around each match. This is keyword search, not a regular expression: "
                "give names and words, not a pattern.",
                {
                    "query": {
                        "type": "string",
                        "description": "Names or words to look for, separated by spaces.",
                    }
                },
                ["query"],
            ),
            _function(
                SYMBOLS,
                "List the functions, classes and methods one file defines, with the "
                "lines they span. Use it to find what to read before reading it.",
                {"path": {"type": "string", "description": "Path relative to the root."}},
                ["path"],
            ),
            _function(
                CALLERS,
                "List the places that call a function or method. Answers only where "
                "this repository has a call-graph index.",
                {"symbol": {"type": "string", "description": "The name to look up."}},
                ["symbol"],
            ),
        ]

    def notes(self) -> str:
        """What the model is told about these tools.

        Every number is read from the values a call actually uses, so what the model is
        told and what it gets cannot drift apart.
        """
        graph = (
            "answers"
            if self.graph is not None and codegraph.available(self.graph, self.repository) is None
            else "has no index for this repository and will say so"
        )
        return f"""

You have tools that read this repository. They are free: they answer out of an index \
already built, they run nothing, and they change nothing.

- `{READ}(path, line)` returns {READ_LINES} lines from that point. Paths are relative \
to the repository root.
- `{SEARCH}(query)` is keyword search over the indexed code, not a regular expression. \
It returns up to {SEARCH_LIMIT} pieces of code that name what you asked for.
- `{SYMBOLS}(path)` lists what one file defines and the lines each spans.
- `{CALLERS}(symbol)` {graph}.
- A result longer than {RESULT_CHARS} characters is cut. Ask for a narrower slice \
rather than the same thing again.

Use them when the diff points at code it does not show. Ask for what you need and then \
answer; you are not being asked to explore the repository.
"""

    # --- the tools ---------------------------------------------------------------

    def handles(self, name: str) -> bool:
        return name in (READ, SEARCH, SYMBOLS, CALLERS)

    def call(self, name: str, arguments: dict[str, Any], log: Logger | None = None) -> str:
        log = (log or create_logger("jobs")).bind(component="lookup")
        started = time.monotonic()
        try:
            if name == READ:
                text = self.read_file(_text(arguments, "path"), _int(arguments, "line"))
            elif name == SEARCH:
                text = self.search(_text(arguments, "query"))
            elif name == SYMBOLS:
                text = self.symbols(_text(arguments, "path"))
            elif name == CALLERS:
                text = self.callers(_text(arguments, "symbol"))
            else:
                return f"There is no {name} tool."
        except OSError as error:
            # A read that fails goes back as text, so the review continues and says
            # what it could not see.
            log.warn("lookup failed", reason="lookup_failed", tool=name, error=error)
            return f"That could not be read: {error}"
        log.debug("lookup answered", tool=name, ms=(time.monotonic() - started) * 1000)
        return text

    def read_file(self, path: str, line: int = 0) -> str:
        found = self._within(path)
        if found is None:
            return f"There is no file at {path!r} in this repository."
        if found.stat().st_size > MAX_FILE_BYTES:
            return f"{path} is larger than {MAX_FILE_BYTES} bytes, so it is not source to read."
        lines = found.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(1, line or 1)
        if first > len(lines):
            return f"{path} has {len(lines)} lines, so line {first} is past its end."
        window = lines[first - 1 : first - 1 + READ_LINES]
        numbered = "\n".join(f"{first + offset}: {text}" for offset, text in enumerate(window))
        last = first + len(window) - 1
        tail = (
            "" if last >= len(lines) else f"\n[{len(lines) - last} more lines below line {last}.]"
        )
        return _cut(f"{path} lines {first}-{last} of {len(lines)}:\n{numbered}{tail}")

    def search(self, query: str) -> str:
        hits = search_text(self.store, query, str(self.repository), limit=SEARCH_LIMIT)
        if not hits:
            return f"Nothing indexed in this repository names {query!r}."
        blocks = [f"--- {hit.label}\n{_cut(hit.text, HIT_CHARS)}" for hit in hits]
        return _cut("\n\n".join(blocks))

    def symbols(self, path: str) -> str:
        found = self._within(path)
        if found is None:
            return f"There is no file at {path!r} in this repository."
        if found.stat().st_size > MAX_FILE_BYTES:
            return f"{path} is larger than {MAX_FILE_BYTES} bytes, so it is not source to read."
        defined = map_file(found, found.read_bytes())
        if not defined:
            return f"No grammar reads {path}, or it defines nothing."
        rows = [
            f"{symbol.kind} {symbol.qualified or symbol.name} "
            f"({symbol.start_line}-{symbol.end_line})"
            for symbol in defined
        ]
        return _cut(f"{path} defines:\n" + "\n".join(rows))

    def callers(self, symbol: str) -> str:
        if self.graph is None:
            return "This repository has no call-graph index, so callers cannot be looked up."
        why = codegraph.available(self.graph, self.repository)
        if why is not None:
            return f"Callers cannot be looked up here: {why}."
        found = codegraph.callers(self.graph, self.repository, symbol)
        if not found:
            return f"The index knows no callers of {symbol!r}."
        rows = [f"{caller.path}:{caller.line}" for caller in found]
        return _cut(f"{symbol} is called from:\n" + "\n".join(rows))

    # --- paths -------------------------------------------------------------------

    def _within(self, path: str) -> Path | None:
        """The file, or None when it is not a readable file inside the repository.

        A review reads a repository the user did not necessarily write, and a path in a
        tool call comes from a model that read that repository. `..` and a symlink both
        leave the tree, so the resolved path is checked against the resolved root.
        """
        text = (path or "").strip()
        if not text:
            return None
        root = self.repository.resolve()
        candidate = (
            (root / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
        )
        if candidate != root and root not in candidate.parents:
            return None
        return candidate if candidate.is_file() else None


def _function(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def _int(arguments: dict[str, Any], key: str) -> int:
    value = arguments.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0

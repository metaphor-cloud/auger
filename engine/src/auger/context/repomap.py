"""The symbol map.

For each file: which symbols it defines, what each one is called, and which lines it
covers. That map is what turns "the diff touched line 42" into "the diff changed
`Gateway.complete`", which is the unit a reviewer and a retriever both work in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auger.context.languages import SYMBOL_TYPES, WRAPPER_TYPES, language_for
from auger.log import Logger, create_logger

_parsers: dict[str, object] = {}


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    start_line: int
    end_line: int
    #: The dotted path to this symbol, for example `Gateway.complete`.
    qualified: str = ""

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


def parser_for(language: str, log: Logger | None = None) -> object | None:
    """Return a cached parser. A grammar that will not load is reported once."""
    if language in _parsers:
        return _parsers[language]
    try:
        from tree_sitter_language_pack import get_parser

        _parsers[language] = get_parser(language)
    except Exception as error:
        (log or create_logger("context")).warn(
            "grammar unavailable", reason="no_grammar", language=language, error=error
        )
        _parsers[language] = None
    return _parsers[language]


def _name_of(node: object) -> str:
    named = node.child_by_field_name("name")  # type: ignore[attr-defined]
    if named is not None and named.text:
        return str(named.text.decode("utf-8", "replace"))
    # A declaration wraps the thing that carries the name, for example `export function`.
    for child in node.named_children:  # type: ignore[attr-defined]
        if child.type in SYMBOL_TYPES:
            inner = child.child_by_field_name("name")
            if inner is not None and inner.text:
                return str(inner.text.decode("utf-8", "replace"))
    return ""


def symbols(source: bytes, language: str, log: Logger | None = None) -> list[Symbol]:
    """Every named symbol in one file, outermost first."""
    parser = parser_for(language, log)
    if parser is None:
        return []
    try:
        tree = parser.parse(source)  # type: ignore[attr-defined]
    except Exception as error:
        (log or create_logger("context")).warn(
            "parse failed", reason="parse_error", language=language, error=error
        )
        return []
    found: list[Symbol] = []
    _walk(tree.root_node, "", found)
    return found


def _walk(node: object, prefix: str, found: list[Symbol]) -> None:
    for child in node.named_children:  # type: ignore[attr-defined]
        if child.type in WRAPPER_TYPES:
            # A decorator or an `export` wraps the symbol. Name the thing inside it.
            _walk(child, prefix, found)
            continue
        if child.type in SYMBOL_TYPES:
            name = _name_of(child)
            if name:
                qualified = f"{prefix}.{name}" if prefix else name
                found.append(
                    Symbol(
                        name=name,
                        kind=child.type,
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        qualified=qualified,
                    )
                )
                _walk(child, qualified, found)
                continue
        _walk(child, prefix, found)


def map_file(path: Path, source: bytes, log: Logger | None = None) -> list[Symbol]:
    language = language_for(path)
    return symbols(source, language, log) if language else []

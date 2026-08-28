"""The map runs against real grammars, in the languages a user actually has."""

from __future__ import annotations

from pathlib import Path

import pytest

from reviewrig.context.languages import indexable, language_for
from reviewrig.context.repomap import map_file

PYTHON = b'''
import os


class Gateway:
    """A gateway."""

    def complete(self, prompt):
        return prompt


def helper(value):
    return value * 2
'''

TYPESCRIPT = b"""
export type Status = "on" | "off";

export function connect(url: string): void {
  return;
}

export class Client {
  send(body: string) {
    return body;
  }
}
"""

RUST = b"""
pub struct Engine {
    port: u16,
}

impl Engine {
    pub fn start(&self) -> u16 {
        self.port
    }
}

fn main() {}
"""

GO = b"""
package main

type Server struct {
	Port int
}

func (s *Server) Start() int {
	return s.Port
}

func main() {}
"""


def names(source: bytes, name: str) -> list[str]:
    return [symbol.qualified for symbol in map_file(Path(name), source)]


def test_it_maps_python() -> None:
    assert names(PYTHON, "a.py") == ["Gateway", "Gateway.complete", "helper"]


def test_it_maps_typescript() -> None:
    found = names(TYPESCRIPT, "a.ts")
    assert "connect" in found
    assert "Client" in found
    assert "Client.send" in found


def test_a_decorator_or_an_export_does_not_become_a_symbol() -> None:
    """`export function connect` is one symbol, not a wrapper and a symbol."""
    assert "export_statement" not in [symbol.kind for symbol in map_file(Path("a.ts"), TYPESCRIPT)]


def test_it_maps_rust() -> None:
    found = names(RUST, "a.rs")
    assert "Engine" in found
    assert "main" in found


def test_it_maps_go() -> None:
    found = names(GO, "a.go")
    assert "Server" in found
    assert "main" in found


def test_it_records_the_line_span() -> None:
    symbol = next(s for s in map_file(Path("a.py"), PYTHON) if s.qualified == "helper")
    assert symbol.start_line == 12
    assert symbol.end_line == 13
    assert symbol.line_count == 2


def test_a_file_with_no_grammar_maps_to_nothing() -> None:
    assert map_file(Path("notes.txt"), b"hello") == []


def test_broken_code_does_not_raise() -> None:
    """Half written code is the normal state of a repository under an agent."""
    assert isinstance(map_file(Path("a.py"), b"def broken(:\n"), list)


@pytest.mark.parametrize(
    ("name", "language"),
    [("a.py", "python"), ("a.tsx", "tsx"), ("a.rs", "rust"), ("a.go", "go"), ("a.txt", None)],
)
def test_the_suffix_chooses_the_grammar(name: str, language: str | None) -> None:
    assert language_for(name) == language


def test_a_lock_file_or_a_huge_file_is_not_indexed() -> None:
    assert indexable("a.py", 100) is True
    assert indexable("pnpm-lock.yaml", 100) is False
    assert indexable("bundle.min.js", 100) is False
    assert indexable("a.py", 0) is False
    assert indexable("a.py", 10_000_000) is False

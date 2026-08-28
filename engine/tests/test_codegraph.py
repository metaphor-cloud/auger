"""A real call graph knows that one function calls another. Search does not."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from reviewrig.config.schema import CodeGraph
from reviewrig.context import codegraph

OUTPUT = json.dumps(
    {
        "symbol": "read",
        "callers": [
            {"name": "writer.ts", "kind": "file", "filePath": "src/writer.ts", "startLine": 12},
            {"name": "main.ts", "kind": "file", "filePath": "src/main.ts", "startLine": 3},
        ],
    }
)


def fake_tool(directory: Path, output: str = OUTPUT, code: int = 0) -> Path:
    """A stand-in for the real tool. The answer lives in a file, so no shell quoting
    can change it."""
    answer = directory / "answer.json"
    answer.write_text(output, encoding="utf-8")
    path = directory / "codegraph"
    # An absolute /bin/cat, because these tests narrow PATH to this directory alone.
    path.write_text(f'#!/bin/sh\n/bin/cat "{answer}"\nexit {code}\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_it_reads_the_caller_files() -> None:
    found = codegraph.parse(OUTPUT, "read")
    assert [caller.path for caller in found] == ["src/writer.ts", "src/main.ts"]
    assert found[0].line == 12


def test_output_it_does_not_understand_means_no_callers() -> None:
    """A tool that changed its shape must not stop a review."""
    assert codegraph.parse("not json", "read") == []
    assert codegraph.parse('{"something": "else"}', "read") == []
    assert codegraph.parse('{"callers": "wrong type"}', "read") == []


def test_a_caller_with_no_file_is_dropped() -> None:
    assert codegraph.parse('{"callers": [{"name": "x"}]}', "read") == []


def test_it_is_off_until_it_is_turned_on(tmp_path: Path) -> None:
    (tmp_path / ".codegraph").mkdir()
    assert codegraph.available(CodeGraph(), tmp_path) == "turned off"


def test_it_says_when_the_tool_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from reviewrig.sandbox import which

    (tmp_path / ".codegraph").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    reason = codegraph.available(CodeGraph(enabled=True), tmp_path)
    assert reason is not None
    assert "not installed" in reason


def test_it_says_when_the_repository_has_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rig reads an index that is there. It never makes one."""
    from reviewrig.sandbox import which

    tools = tmp_path / "bin"
    tools.mkdir()
    fake_tool(tools)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    reason = codegraph.available(CodeGraph(enabled=True), tmp_path)
    assert reason is not None
    assert "no .codegraph index" in reason


def test_it_asks_only_when_everything_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reviewrig.sandbox import which

    repository = tmp_path / "repo"
    (repository / ".codegraph").mkdir(parents=True)
    tools = tmp_path / "bin"
    tools.mkdir()
    fake_tool(tools)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())

    config = CodeGraph(enabled=True)
    assert codegraph.available(config, repository) is None
    found = codegraph.callers_for(config, repository, ["read"])
    assert [caller.path for caller in found] == ["src/writer.ts", "src/main.ts"]


def test_a_tool_that_fails_returns_no_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reviewrig.sandbox import which

    repository = tmp_path / "repo"
    (repository / ".codegraph").mkdir(parents=True)
    tools = tmp_path / "bin"
    tools.mkdir()
    fake_tool(tools, output="boom", code=1)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    assert codegraph.callers_for(CodeGraph(enabled=True), repository, ["read"]) == []


def test_the_same_caller_is_not_returned_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reviewrig.sandbox import which

    repository = tmp_path / "repo"
    (repository / ".codegraph").mkdir(parents=True)
    tools = tmp_path / "bin"
    tools.mkdir()
    fake_tool(tools)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(which, "EXTRA_PATHS", ())
    found = codegraph.callers_for(CodeGraph(enabled=True), repository, ["read", "write"])
    assert len(found) == 2

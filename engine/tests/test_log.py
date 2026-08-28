from __future__ import annotations

import json

import pytest

from auger.log import create_logger, serialise


def test_a_line_is_one_json_object_with_level_and_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_logger("test").info("repo scanned", count=3)
    line = json.loads(capsys.readouterr().out.strip())
    assert line == {
        "level": "info",
        "message": "repo scanned",
        "component": "test",
        "data": {"count": 3},
    }


def test_warn_and_error_go_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    log = create_logger("test")
    log.warn("repo skipped", reason="busy")
    log.error("run failed", reason="timeout")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 2


def test_bind_adds_context_to_every_line(capsys: pytest.CaptureFixture[str]) -> None:
    create_logger("test").bind(job_id="j1").info("started")
    assert json.loads(capsys.readouterr().out.strip())["job_id"] == "j1"


def test_debug_is_off_below_its_level(capsys: pytest.CaptureFixture[str]) -> None:
    create_logger("test", "info").debug("noise")
    assert capsys.readouterr().out == ""


def test_an_exception_keeps_its_stack() -> None:
    try:
        raise ValueError("bad input")
    except ValueError as error:
        result = serialise(error)
    assert isinstance(result, dict)
    assert result["name"] == "ValueError"
    assert "ValueError: bad input" in result["stack"]


def test_a_broken_stream_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host owns the read end of these pipes. A dead host must not kill the engine."""

    class BrokenStream:
        def write(self, _: str) -> int:
            raise BrokenPipeError("host is gone")

        def flush(self) -> None:
            raise BrokenPipeError("host is gone")

    monkeypatch.setattr("sys.stderr", BrokenStream())
    monkeypatch.setattr("sys.stdout", BrokenStream())
    log = create_logger("test")
    log.warn("host process is gone, stopping", reason="parent_gone")
    log.info("still alive")

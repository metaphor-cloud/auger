"""A local model does not always answer with clean JSON."""

from __future__ import annotations

import json

import pytest

from auger.jobs.parse import extract_object, parse_findings

ONE = {"findings": [{"file": "a.py", "title": "Leak", "severity": "high", "line": 12}]}


def test_it_reads_a_plain_object() -> None:
    findings, problems = parse_findings(json.dumps(ONE))
    assert problems == []
    assert findings[0].file == "a.py"


def test_it_reads_an_object_inside_a_fence() -> None:
    findings, _ = parse_findings(f"```json\n{json.dumps(ONE)}\n```")
    assert findings[0].title == "Leak"


def test_it_reads_an_object_after_a_sentence() -> None:
    findings, _ = parse_findings(f"Here is my review.\n\n{json.dumps(ONE)}\n\nThat is all.")
    assert findings[0].title == "Leak"


def test_it_reads_one_finding_that_was_not_wrapped() -> None:
    findings, _ = parse_findings(json.dumps(ONE["findings"][0]))
    assert findings[0].file == "a.py"


def test_an_empty_list_is_a_valid_answer() -> None:
    """A correct change must be reportable as no findings."""
    findings, problems = parse_findings('{"findings": []}')
    assert findings == []
    assert problems == []


def test_a_line_number_as_text_still_works() -> None:
    findings, _ = parse_findings('{"findings":[{"file":"a.py","title":"x","line":"12"}]}')
    assert findings[0].line == 12


@pytest.mark.parametrize("line", ["0", "-3", "unknown", "null"])
def test_a_line_number_that_makes_no_sense_becomes_none(line: str) -> None:
    findings, _ = parse_findings(f'{{"findings":[{{"file":"a.py","title":"x","line":"{line}"}}]}}')
    assert findings[0].line is None


def test_an_unknown_severity_becomes_medium() -> None:
    findings, _ = parse_findings('{"findings":[{"file":"a.py","title":"x","severity":"BAD"}]}')
    assert findings[0].severity == "medium"


def test_severity_case_does_not_matter() -> None:
    findings, _ = parse_findings('{"findings":[{"file":"a.py","title":"x","severity":"HIGH"}]}')
    assert findings[0].severity == "high"


def test_one_bad_entry_does_not_lose_the_good_ones() -> None:
    findings, problems = parse_findings(
        '{"findings":[{"no":"file"}, {"file":"a.py","title":"ok"}, "text"]}'
    )
    assert [finding.title for finding in findings] == ["ok"]
    assert len(problems) == 2


def test_an_answer_with_no_json_says_so() -> None:
    findings, problems = parse_findings("I cannot review this.")
    assert findings == []
    assert problems == ["the answer held no JSON object"]


def test_an_answer_with_no_findings_key_says_so() -> None:
    _, problems = parse_findings('{"result": "fine"}')
    assert problems == ["the answer had no `findings` list"]


def test_a_brace_inside_a_string_does_not_end_the_object() -> None:
    text = '{"findings":[{"file":"a.py","title":"uses } literally","severity":"low"}]}'
    findings, _ = parse_findings(text)
    assert findings[0].title == "uses } literally"


def test_it_finds_the_object_when_the_text_holds_stray_braces() -> None:
    assert extract_object('} leading junk {"findings": []}') == {"findings": []}

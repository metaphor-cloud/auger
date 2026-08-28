"""Read the reviewer's answer.

A local model does not always answer with clean JSON. It wraps the object in a fence, or
it writes a sentence first. The parser takes the first balanced object it can find and
drops any finding that does not fit the shape, rather than failing the whole run over one
malformed entry.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from reviewrig.store.findings import SEVERITY_ORDER, Severity

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class RawFinding(BaseModel):
    file: str
    title: str
    detail: str = ""
    severity: Severity = "medium"
    line: int | None = None
    suggestion: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("severity", mode="before")
    @classmethod
    def _known_severity(cls, value: object) -> str:
        text = str(value).strip().lower()
        return text if text in SEVERITY_ORDER else "medium"

    @field_validator("line", mode="before")
    @classmethod
    def _line_number(cls, value: object) -> int | None:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None


def extract_object(text: str) -> dict[str, Any] | None:
    """Return the first balanced JSON object in `text`."""
    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _candidates(text: str) -> list[str]:
    found = [match.group(1).strip() for match in FENCE.finditer(text)]
    found.append(text.strip())
    balanced = _balanced_object(text)
    if balanced:
        found.append(balanced)
    return [candidate for candidate in found if candidate]


def _balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_findings(text: str) -> tuple[list[RawFinding], list[str]]:
    """Return the findings that fit the shape, and one message per entry that did not."""
    body = extract_object(text)
    if body is None:
        return [], ["the answer held no JSON object"]
    entries = body.get("findings")
    if entries is None and isinstance(body.get("file"), str):
        entries = [body]  # A model that returned one finding, unwrapped.
    if not isinstance(entries, list):
        return [], ["the answer had no `findings` list"]
    findings: list[RawFinding] = []
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"finding {index} was not an object")
            continue
        try:
            findings.append(RawFinding.model_validate(entry))
        except ValidationError as error:
            problems.append(f"finding {index} did not fit: {error.errors()[0]['msg']}")
    return findings, problems

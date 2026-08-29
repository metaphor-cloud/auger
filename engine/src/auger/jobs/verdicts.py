"""What a judgement of a finding looks like, and how to read one.

The second model answers in this shape. It lives apart from whoever asks, because the
asking has changed twice and the shape has not.
"""

from __future__ import annotations

from typing import Literal, cast

Verdict = Literal["true", "false", "uncertain"]
VERDICTS: frozenset[str] = frozenset({"true", "false", "uncertain"})

#: The shape a verdict takes, held to by the decoder.
VERDICT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["true", "false", "uncertain"]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "verdict"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def parse_verdicts(text: str, count: int) -> tuple[dict[int, tuple[Verdict, str]], list[str]]:
    """Read the answer. An item the model skipped keeps no verdict at all."""
    from auger.jobs.parse import extract_object

    body = extract_object(text)
    if body is None:
        return {}, ["the answer held no JSON object"]
    entries = body.get("verdicts")
    if not isinstance(entries, list):
        return {}, ["the answer had no `verdicts` list"]
    verdicts: dict[int, tuple[Verdict, str]] = {}
    problems: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("id", 0))
        except (TypeError, ValueError):
            continue
        verdict = str(entry.get("verdict", "")).strip().lower()
        if not 1 <= index <= count:
            problems.append(f"verdict for unknown item {index}")
            continue
        if verdict not in VERDICTS:
            problems.append(f"item {index} got an unknown verdict {verdict!r}")
            continue
        verdicts[index] = (cast(Verdict, verdict), str(entry.get("reason", "")).strip()[:200])
    return verdicts, problems

"""Ask the model which static findings are real.

Semgrep is fast and it does not understand the code around a match. It reports the
pattern, not the problem, so a large share of what it finds is already handled, already
unreachable, or already intended.

Only the findings and their context go to the model, never the whole repository, which
is why an audit costs a fraction of a review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, cast

from auger.config import Policy
from auger.config.schema import JobClass
from auger.llm import Gateway, Message, ModelError
from auger.log import Logger, create_logger
from auger.store import Store
from auger.store.findings import Finding, set_triage

Verdict = Literal["true", "false", "uncertain"]
VERDICTS: frozenset[str] = frozenset({"true", "false", "uncertain"})

#: How many findings go into one request. A large batch loses the model's attention.
BATCH = 12

SYSTEM = """\
You judge the output of a static analysis tool. Each item is a rule that matched a line \
of code.

For each item, decide whether it is a real problem in this code.

- "true": the problem is real here.
- "false": the rule matched, but this code is not affected. The value is a constant, the \
input is already checked, the path cannot run, or the pattern is the intended use.
- "uncertain": you cannot tell from what you were given.

Answer with one JSON object and nothing else:

{"verdicts": [{"id": 1, "verdict": "false", "reason": "one short line"}]}
"""


@dataclass
class TriageOutcome:
    judged: int = 0
    real: int = 0
    dismissed: int = 0
    uncertain: int = 0
    problems: list[str] | None = None


def item_text(index: int, finding: Finding) -> str:
    where = f"{finding.file}:{finding.line}" if finding.line else finding.file
    rule = finding.snippet.split("\n", 1)[0]
    code = finding.snippet.split("\n", 1)[1] if "\n" in finding.snippet else ""
    return "\n".join(
        [
            f"id: {index}",
            f"rule: {rule}",
            f"where: {where}",
            f"says: {finding.detail}",
            "code:",
            code.strip() or "(not recorded)",
        ]
    )


def messages_for(findings: list[Finding]) -> list[Message]:
    body = "\n\n---\n\n".join(
        item_text(index, finding) for index, finding in enumerate(findings, start=1)
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=body)]


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


async def triage(
    store: Store,
    gateway: Gateway,
    findings: list[Finding],
    policy: Policy,
    log: Logger | None = None,
) -> TriageOutcome:
    """Judge every finding, in batches. Never raises."""
    log = (log or create_logger("jobs")).bind(component="triage")
    outcome = TriageOutcome(problems=[])
    for start in range(0, len(findings), BATCH):
        batch = findings[start : start + BATCH]
        try:
            completion = await gateway.complete(
                JobClass.TRIAGE, messages_for(batch), profile=policy.model_profile
            )
        except ModelError as error:
            # An untriaged finding still shows. Losing it would be worse than showing it.
            log.warn("triage skipped", reason="model_failed", count=len(batch), error=error)
            outcome.problems.append(str(error))  # type: ignore[union-attr]
            continue
        verdicts, problems = parse_verdicts(completion.text, len(batch))
        outcome.problems.extend(problems)  # type: ignore[union-attr]
        for index, (verdict, reason) in verdicts.items():
            finding = batch[index - 1]
            set_triage(store, finding.fingerprint, verdict, reason)
            outcome.judged += 1
            if verdict == "true":
                outcome.real += 1
            elif verdict == "false":
                outcome.dismissed += 1
            else:
                outcome.uncertain += 1
    log.info(
        "triage finished",
        judged=outcome.judged,
        real=outcome.real,
        dismissed=outcome.dismissed,
        uncertain=outcome.uncertain,
    )
    return outcome


def cost_estimate(findings: list[Finding]) -> int:
    """Characters sent for a triage. The UI shows what an audit costs."""
    return sum(
        len(json.dumps(item_text(index, finding))) for index, finding in enumerate(findings, 1)
    )

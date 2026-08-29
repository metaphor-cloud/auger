"""A second model, arguing with the first.

One model reviewing its own work agrees with itself. Two models from different families
disagree about different things, and the disagreements are where the false findings are.

The second model sees the same change and the findings drawn from it, and says of each
whether the code shown actually supports it. A finding it rejects is marked dismissed,
not deleted: the disagreement is worth seeing, and the model doing the rejecting is not
always right either.

The two roles swap between runs when `alternate` is on, so neither model's blind spots
decide on their own. Which one reviewed is recorded on the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from auger.config import Policy
from auger.config.schema import JobClass
from auger.jobs.parse import as_response_format
from auger.jobs.triage import VERDICT_SCHEMA, parse_verdicts
from auger.llm import Gateway, Message, ModelError
from auger.log import Logger, create_logger
from auger.store import Store
from auger.store.findings import Finding, set_triage

#: How many findings go into one request. The diff goes with them, so this is smaller
#: than the static triage batch.
BATCH = 8

SYSTEM = """\
Another reviewer read the change below and reported the findings beneath it. You did not
write either. Judge each finding against the change itself.

- "true": the change shows this, and the consequence described follows from it.
- "false": the change does not show it. The claim is about code that is not here, the
  problem is already handled a line away, the reviewer misread what the code does, or
  the consequence does not follow.
- "uncertain": the change alone cannot settle it.

You are not being asked to review the change. You are being asked whether these findings
are true of it. A reviewer who reports something plausible that the code does not show is
worse than one who reports nothing, so say "false" when the evidence is not here.

Answer with one JSON object and nothing else:

{"verdicts": [{"id": 1, "verdict": "false", "reason": "one short line"}]}
"""


@dataclass
class Argument:
    """What the second model made of the first one's findings."""

    judged: int = 0
    kept: int = 0
    rejected: int = 0
    uncertain: int = 0
    #: Which backend judged. Recorded so a disagreement can be traced to a model.
    backend: str = ""
    problems: list[str] | None = None


def item_text(index: int, finding: Finding) -> str:
    where = f"{finding.file}:{finding.line}" if finding.line else finding.file
    return "\n".join(
        [
            f"id: {index}",
            f"where: {where}",
            f"severity: {finding.severity}",
            f"claim: {finding.title}",
            f"says: {finding.detail}",
        ]
    )


def messages_for(diff: str, findings: list[Finding]) -> list[Message]:
    items = "\n\n---\n\n".join(
        item_text(index, finding) for index, finding in enumerate(findings, start=1)
    )
    body = "\n".join(["The change:", "```diff", diff.rstrip(), "```", "", "The findings:", items])
    return [Message(role="system", content=SYSTEM), Message(role="user", content=body)]


async def argue(
    store: Store,
    gateway: Gateway,
    diff: str,
    findings: list[Finding],
    policy: Policy,
    log: Logger | None = None,
) -> Argument:
    """Have the other model judge these findings. Never raises."""
    log = (log or create_logger("jobs")).bind(component="adversary")
    outcome = Argument(problems=[])
    if not findings:
        return outcome

    for start in range(0, len(findings), BATCH):
        batch = findings[start : start + BATCH]
        try:
            completion = await gateway.complete(
                JobClass.VERIFY,
                messages_for(diff, batch),
                profile=policy.model_profile,
                response_format=as_response_format(VERDICT_SCHEMA, "verdicts"),
            )
        except ModelError as error:
            # An unjudged finding still shows. Losing it would be worse than showing it.
            log.warn("argument skipped", reason="model_failed", count=len(batch), error=error)
            outcome.problems.append(str(error))  # type: ignore[union-attr]
            continue
        outcome.backend = completion.backend
        verdicts, problems = parse_verdicts(completion.text, len(batch))
        outcome.problems.extend(problems)  # type: ignore[union-attr]
        for index, (verdict, reason) in verdicts.items():
            finding = batch[index - 1]
            set_triage(store, finding.fingerprint, verdict, f"{completion.model}: {reason}")
            outcome.judged += 1
            if verdict == "true":
                outcome.kept += 1
            elif verdict == "false":
                outcome.rejected += 1
            else:
                outcome.uncertain += 1

    log.info(
        "argument finished",
        backend=outcome.backend,
        judged=outcome.judged,
        kept=outcome.kept,
        rejected=outcome.rejected,
        uncertain=outcome.uncertain,
    )
    return outcome

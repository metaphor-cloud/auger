"""A second model, arguing with the first.

One model reviewing its own work agrees with itself. Two models from different families
disagree about different things, and the disagreements are where the false findings are.

Two capable models do not fit in memory at once, so this does not run inline after a
review. Findings accumulate, and a sweep loads the second model once, judges everything
waiting, and gives the memory back. That fits a rig that runs all day: nobody is waiting
on the answer, and a swap costs minutes rather than being paid per finding.

The second model is shown the code as it stands, not the change that produced the
finding, because by the time the sweep runs the change is history. It marks what it
rejects rather than deleting it: the disagreement is worth seeing, and the model doing
the rejecting is not always right either.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auger.config import Policy
from auger.config.schema import JobClass
from auger.jobs.parse import as_response_format
from auger.jobs.triage import VERDICT_SCHEMA, parse_verdicts
from auger.llm import Gateway, Message, ModelError
from auger.log import Logger, create_logger
from auger.store import Store
from auger.store.findings import Finding, set_triage

#: How many findings go into one request. The code goes with each, so this is smaller
#: than the static triage batch.
BATCH = 6
#: Lines of code shown either side of a finding.
RADIUS = 30
#: How much of one file is shown when the finding names no line.
HEAD_LINES = 80

SYSTEM = """\
Another reviewer read this code and reported the findings below. You did not write
either. Judge each finding against the code shown.

- "true": the code shows this, and the consequence described follows from it.
- "false": the code does not show it. The claim is about code that is not here, the
  problem is handled a few lines away, the reviewer misread what the code does, the
  construct is the ordinary one for this language or tool, or the consequence does not
  follow from the cause.
- "uncertain": the code shown cannot settle it.

You are not being asked to review the code. You are being asked whether these findings
are true of it.

Two things to weigh. A reviewer that reports something plausible the code does not show
is worse than one that reports nothing, so answer "false" when the evidence is not
there. And a claim about a framework, a build tool, or a configuration format is only
true if that is really how the tool behaves: say "false" when the reviewer has the
tool's own rules backwards.

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


def code_for(finding: Finding, radius: int = RADIUS) -> str:
    """The code a finding points at, as it stands now.

    Numbered, so the model can see whether the line the finding names is the line it
    describes. A file that has since been deleted gives nothing, and the judgement says
    so rather than guessing.
    """
    path = Path(finding.repo_path) / finding.file
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return ""
    if not lines:
        return ""
    if finding.line is None:
        shown = range(0, min(len(lines), HEAD_LINES))
    else:
        start = max(0, finding.line - 1 - radius)
        shown = range(start, min(len(lines), finding.line + radius))
    return "\n".join(f"{index + 1}: {lines[index]}" for index in shown)


def item_text(index: int, finding: Finding) -> str:
    where = f"{finding.file}:{finding.line}" if finding.line else finding.file
    code = code_for(finding)
    return "\n".join(
        [
            f"id: {index}",
            f"where: {where}",
            f"reported by: {finding.source}",
            f"severity: {finding.severity}",
            f"claim: {finding.title}",
            f"says: {finding.detail}",
            "the code:",
            code or "(the file this names could not be read, so nothing supports it)",
        ]
    )


def messages_for(findings: list[Finding]) -> list[Message]:
    body = "\n\n---\n\n".join(
        item_text(index, finding) for index, finding in enumerate(findings, start=1)
    )
    return [Message(role="system", content=SYSTEM), Message(role="user", content=body)]


async def argue(
    store: Store,
    gateway: Gateway,
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
                messages_for(batch),
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

"""Look at a whole repository, not at a change.

A diff review reads what moved. It cannot see a module that duplicates another, an error
path that no layer handles, or a symbol that nothing calls any more. An audit reads the
shape of the repository instead: every file, every symbol, and how big each one is.

The outline is sent, not the code. A repository of a thousand files fits in one prompt as
an outline and does not fit at all as source.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from reviewrig.config import Policy
from reviewrig.config.schema import JobClass
from reviewrig.context import reindex
from reviewrig.jobs.parse import parse_findings
from reviewrig.llm import Gateway, Message, ModelError
from reviewrig.log import Logger, create_logger
from reviewrig.models import Repository
from reviewrig.store import Store
from reviewrig.store.findings import Finding, record
from reviewrig.store.runs import Run, finish, set_audited, start

KIND = "audit"
#: How much of the outline reaches the model. Beyond this the model stops reading.
OUTLINE_BUDGET = 40_000
#: Files listed, largest first. A repository has a long tail of small files.
MAX_FILES = 400

SYSTEM = """\
You audit a whole repository. You are given its outline: every file, and the symbols each \
file defines with their size in lines.

Report only problems that a review of one change cannot see:

- two modules that do the same work;
- a symbol that nothing appears to use;
- a layer with no error handling where every other layer has it;
- a file that carries far more than its name suggests;
- a missing piece that the rest of the structure implies.

Do not report style, naming, or a preference. Do not report a defect inside a function: \
you cannot see the code. If the structure is sound, return an empty list.

Answer with one JSON object and nothing else:

{"findings": [{"file": "path", "line": null, "severity": "medium", \
"title": "one short line", "detail": "what is wrong and what it costs", \
"suggestion": "the smallest change that fixes it", "confidence": 0.6}]}
"""


@dataclass(frozen=True)
class AuditOutcome:
    run: Run
    findings: list[Finding]
    outline_bytes: int = 0
    problems: list[str] | None = None


def outline(store: Store, repository: Path, budget: int = OUTLINE_BUDGET) -> str:
    """The shape of the repository: files, their symbols, and their sizes."""
    rows = store.query(
        """
        SELECT path, symbol, kind, start_line, end_line
        FROM chunks WHERE repo_path = ? ORDER BY path, start_line
        """,
        (str(repository),),
    )
    by_file: dict[str, list[str]] = {}
    for row in rows:
        symbol = str(row["symbol"]).split(" part ")[0]
        if not symbol:
            continue
        lines = int(row["end_line"]) - int(row["start_line"]) + 1
        entries = by_file.setdefault(str(row["path"]), [])
        entry = f"{symbol} ({lines})"
        if entry not in entries:
            entries.append(entry)

    ordered = sorted(by_file.items(), key=lambda item: -len(item[1]))[:MAX_FILES]
    parts: list[str] = []
    used = 0
    for path, symbols in sorted(ordered):
        block = f"{path}: " + ", ".join(symbols)
        if used + len(block) > budget:
            parts.append(f"[{len(ordered) - len(parts)} more files not listed]")
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


async def audit(
    store: Store,
    gateway: Gateway,
    repository: Repository,
    policy: Policy,
    log: Logger | None = None,
) -> AuditOutcome:
    """Audit one repository. Never raises."""
    log = (log or create_logger("jobs")).bind(repo=repository.slug, kind=KIND)
    started = time.monotonic()
    run = start(store, repository.path, KIND, None, None)
    log = log.bind(run=run.id)

    await reindex(store, gateway, repository.path, policy.model_profile, log)
    shape = outline(store, repository.path)
    if not shape.strip():
        run.status = "skipped"
        run.reason = "empty_index"
        run.duration_ms = int((time.monotonic() - started) * 1000)
        log.info("audit skipped", reason="empty_index")
        return AuditOutcome(finish(store, run), [])

    hints = (
        f"\n\nThe repository owner wrote: {policy.hints.strip()}" if policy.hints.strip() else ""
    )
    system = SYSTEM
    if policy.instructions.strip():
        system += (
            "\nThe person running this review added the instructions below. Follow "
            "them.\n\n" + policy.instructions.strip() + "\n"
        )
    messages = [
        Message(role="system", content=system),
        Message(
            role="user",
            content=f"Repository: {repository.slug}{hints}\n\nOutline:\n{shape}",
        ),
    ]
    try:
        completion = await gateway.complete(JobClass.REVIEW, messages, profile=policy.model_profile)
    except ModelError as error:
        run.status = "failed"
        run.reason = "model_failed"
        run.error = str(error)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        log.error("audit failed", reason="model_failed", error=error)
        return AuditOutcome(finish(store, run), [])

    raw, problems = parse_findings(completion.text)
    findings = [
        Finding(
            repo_path=str(repository.path),
            source="audit",
            severity=item.severity,
            category=item.category,
            title=item.title.strip(),
            detail=item.detail.strip(),
            suggestion=item.suggestion.strip(),
            file=item.file.strip(),
            line=item.line,
            confidence=item.confidence,
            snippet=f"audit:{item.file.strip()}",
            run_id=run.id,
        )
        for item in raw
    ]
    record(store, findings)
    set_audited(store, repository.path)

    run.status = "ok"
    run.finding_count = len(findings)
    run.prompt_tokens = completion.prompt_tokens
    run.completion_tokens = completion.completion_tokens
    run.backend = completion.backend
    run.duration_ms = int((time.monotonic() - started) * 1000)
    if problems:
        run.error = "; ".join(problems[:3])
    log.info("audit finished", findings=len(findings), outline_bytes=len(shape))
    return AuditOutcome(finish(store, run), findings, len(shape), problems)

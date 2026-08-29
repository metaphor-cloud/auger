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

from auger.config import Policy
from auger.config.schema import JobClass
from auger.context import reindex
from auger.jobs.parse import FINDINGS_SCHEMA, as_response_format, parse_findings
from auger.jobs.triage import triage_claims
from auger.llm import Gateway, Message, ModelError
from auger.log import Logger, create_logger
from auger.models import Repository
from auger.store import Store
from auger.store.findings import Finding, close_missing, record
from auger.store.runs import Run, finish, set_audited, start

KIND = "audit"
#: How much of the outline reaches the model. Beyond this the model stops reading.
OUTLINE_BUDGET = 40_000
#: Files listed, largest first. A repository has a long tail of small files.
MAX_FILES = 400

SYSTEM = """\
You audit a whole repository. You are given its outline and nothing else: every file, \
and the names of the symbols each file defines with their size in lines.

You cannot see any code. You cannot see what calls what, what a symbol contains, or \
whether two symbols with the same name are the same thing. Many languages declare one \
type across several places: a class and its extension, a type and its conformance, a \
declaration and its implementation. Two entries with one name is normal and is not a \
finding.

Report only what an outline can show:

- a file that carries far more than its name suggests;
- a layer whose files are laid out unlike every other layer of the same kind;
- a missing piece that the rest of the structure clearly implies, such as a module with \
no tests beside it where every sibling has them;
- a directory whose contents contradict its name.

Do not report a duplicate, an unused symbol, or a defect inside a function. Judging any \
of those needs the code, and you do not have it. Do not report style or naming. If the \
structure is sound, return an empty list, which is the usual answer.

Say plainly in `detail` what you are inferring from, so a reader can check it.

Answer with one JSON object and nothing else:

{"findings": [{"file": "path", "line": null, "severity": "medium", \
"category": "quality", "title": "one short line", \
"detail": "what is wrong, and what in the outline shows it", \
"suggestion": "the smallest change that fixes it", "confidence": 0.6}]}

severity is one of: critical, high, medium, low, info.
category is one of: security, correctness, performance, quality.
"""


def evidence_for(shape: str, path: str, neighbours: int = 6) -> str:
    """The outline rows a claim can be checked against: its own path, and its siblings.

    A claim about one file is usually a claim about where it sits, so the rest of its
    directory is the evidence that shows whether it is unusual.
    """
    wanted = path.strip()
    directory = wanted.rsplit("/", 1)[0] if "/" in wanted else ""
    lines = shape.splitlines()
    mine = [line for line in lines if line.startswith(f"{wanted}:")]
    beside = [
        line
        for line in lines
        if line not in mine and line.startswith(f"{directory}/" if directory else "")
    ][:neighbours]
    return "\n".join(mine + beside)


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
    # A long symbol is stored as several chunks: the symbol, then `name part 1`,
    # `name part 2`, and so on. Listed separately they look like several symbols of one
    # name, and an audit reads that as a duplicate definition. It is not: it is one
    # symbol that did not fit in a chunk. Collapse the parts and measure the whole span.
    spans: dict[tuple[str, str], tuple[int, int]] = {}
    order: dict[str, list[str]] = {}
    for row in rows:
        symbol = str(row["symbol"]).split(" part ")[0]
        if not symbol:
            continue
        path = str(row["path"])
        key = (path, symbol)
        start, end = int(row["start_line"]), int(row["end_line"])
        if key in spans:
            was = spans[key]
            spans[key] = (min(was[0], start), max(was[1], end))
        else:
            spans[key] = (start, end)
            order.setdefault(path, []).append(symbol)

    by_file: dict[str, list[str]] = {
        path: [
            f"{symbol} ({spans[(path, symbol)][1] - spans[(path, symbol)][0] + 1})"
            for symbol in symbols
        ]
        for path, symbols in order.items()
    }

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
        completion = await gateway.complete(
            JobClass.REVIEW,
            messages,
            profile=policy.model_profile,
            response_format=as_response_format(FINDINGS_SCHEMA),
        )
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
    # An audit reads the whole outline, so the same rule applies as for a scan: what it
    # no longer reports is no longer a finding. This is what clears the duplicates the
    # split-symbol bug produced, without anybody reading 386 of them by hand.
    closed = close_missing(
        store,
        repository.path,
        "audit",
        [finding.fingerprint for finding in findings],
        "the audit no longer reports it",
    )
    if closed:
        log.info("findings closed", count=closed, reason="not_reported")
    set_audited(store, repository.path)

    # A claim drawn from an outline is a guess until something checks it. The same pass
    # that judges the scan's findings judges these, against the outline they came from.
    if findings:
        await triage_claims(
            store,
            gateway,
            [(finding, evidence_for(shape, finding.file)) for finding in findings],
            policy,
            log,
        )

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

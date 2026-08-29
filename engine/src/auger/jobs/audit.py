"""Look at a whole repository, not at a change.

A diff review reads what moved. It never reaches a file nobody has touched, so a defect
that has sat there since the file was written is never seen. An audit goes looking.

A repository does not fit in one prompt, so the audit runs in two passes. The first is
given the outline, which is every file with the symbols it defines and their sizes, plus
whatever Semgrep flagged, and it answers one question: which files are worth reading?
The second pass reads those files and reviews the code in them, the same way a diff
review reviews a change.

Semgrep is a signal about where to look, never a verdict. It matches a pattern without
reading what surrounds it, so its output was mostly noise when it went straight into the
list, and asking a model to judge a one-line match is asking it to guess.

The outline chooses. It never decides. A claim drawn from file names alone is a guess
about code nobody read, and this used to report those guesses as findings.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from auger.config import Policy
from auger.config.schema import JobClass
from auger.context import reindex
from auger.jobs.parse import (
    FINDINGS_SCHEMA,
    as_response_format,
    extract_object,
    parse_findings,
)
from auger.jobs.semgrep import scan
from auger.llm import Gateway, Message, ModelError
from auger.log import Logger, create_logger
from auger.models import Repository
from auger.sandbox import Sandbox
from auger.store import Store
from auger.store.findings import Finding, close_missing, record
from auger.store.runs import Run, finish, set_audited, start

KIND = "audit"
#: How much of the outline reaches the model. Beyond this the model stops reading.
OUTLINE_BUDGET = 40_000
#: Files listed, largest first. A repository has a long tail of small files.
MAX_FILES = 400

#: How many files one audit reads. The point is to cover a repository over many audits,
#: not to read all of it in one, which no context holds and no machine has time for.
READ_FILES = 6
#: How much source goes into one request. A file longer than this is sent truncated,
#: with a line saying so, because half a file reviewed beats a file skipped.
FILE_BUDGET = 24_000

#: How many of the read files are chosen by Semgrep rather than by the model. A scanner
#: that finds something real must not be talked out of it by a model reading names.
FLAGGED_FILES = 2
#: Files named in the prompt as flagged. Beyond this it is a list nobody reads.
FLAGGED_SHOWN = 25

CHOOSE = """\
You are choosing which files to read.

You are given the outline of a repository: every file, and the names of the symbols each \
file defines with their size in lines. The outline may be cut short, so say nothing \
about what is absent from it.

You may also be given what a static analyser flagged. It matches patterns without \
reading the code around them, so most of what it flags is not a problem. Treat it as a \
place to look, never as a defect: it tells you nothing about whether that code is wrong.

Pick the files most likely to hold a real defect, and say in one line why each one. \
Prefer:

- code that handles untrusted input, authentication, tokens, paths or shell commands;
- concurrency, locking, retries, and anything that touches money or time;
- a very large function, where a defect has room to hide;
- a file whose name promises something its symbols do not deliver.

Pick nothing on the strength of a name alone if the outline shows something better. You \
are not reporting defects here and you have not seen any code, so do not describe one.

Answer with one JSON object and nothing else:

{"files": [{"path": "path/from/the/outline.py", "why": "one short line"}]}
"""

SYSTEM = """\
You review whole files for defects. You are given the source of each file, with line \
numbers, and nothing is hidden from you.

Report a defect only where you can point at the line that has it. For each one, say what \
goes wrong, when it goes wrong, and what the consequence is. A reader has to be able to \
check you against the code in front of them.

Do not report style, naming, formatting, or a preference. Do not report a defect you \
cannot see in the lines you were given: if a function calls something you were not \
shown, you do not know that it is wrong. If the code is sound, return an empty list, \
which is a normal answer.

Answer with one JSON object and nothing else:

{"findings": [{"file": "path", "line": 42, "severity": "medium", \
"category": "quality", "title": "one short line", \
"detail": "what goes wrong, when, and what it costs", \
"suggestion": "the smallest change that fixes it", "confidence": 0.6}]}

severity is one of: critical, high, medium, low, info.
category is one of: security, correctness, performance, quality.
"""


@dataclass(frozen=True)
class AuditOutcome:
    run: Run
    findings: list[Finding]
    outline_bytes: int = 0
    problems: list[str] | None = None
    #: The files the first pass chose and the second pass actually read.
    read: tuple[str, ...] = ()


CHOICE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "why": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["files"],
    "additionalProperties": False,
}


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


def numbered(source: str, budget: int = FILE_BUDGET) -> str:
    """The file with a line number against every line.

    A finding has to point at a line, and a model counting newlines itself gets it
    wrong. Long files are cut rather than dropped, and the cut says so.
    """
    lines = source.splitlines()
    out: list[str] = []
    used = 0
    for index, line in enumerate(lines, start=1):
        row = f"{index:5} {line}"
        if used + len(row) > budget:
            out.append(f"... cut here. {len(lines) - index + 1} more lines in this file.")
            break
        out.append(row)
        used += len(row) + 1
    return "\n".join(out)


def wanted_files(text: str, known: set[str], limit: int = READ_FILES) -> list[str]:
    """The paths the first pass asked for, keeping only ones that exist.

    A model asked to name a file will sometimes name one it inferred rather than one it
    read. Reviewing a file that is not there would report on nothing.
    """
    body = extract_object(text)
    entries = body.get("files") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        return []
    chosen: list[str] = []
    for entry in entries:
        path = str(entry.get("path", "")).strip() if isinstance(entry, dict) else ""
        if path in known and path not in chosen:
            chosen.append(path)
        if len(chosen) >= limit:
            break
    return chosen


def flagged_text(matches: list[Finding], shown: int = FLAGGED_SHOWN) -> str:
    """What the scanner flagged, by file, worst first. Empty when it flagged nothing."""
    if not matches:
        return ""
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    by_file: dict[str, list[Finding]] = {}
    for one in matches:
        by_file.setdefault(one.file, []).append(one)
    order = sorted(
        by_file.items(),
        key=lambda item: (min(rank.get(one.severity, 5) for one in item[1]), -len(item[1])),
    )
    rows = [
        f"{path}: " + ", ".join(sorted({one.title for one in found})[:4])
        for path, found in order[:shown]
    ]
    return "\n".join(rows)


def flagged_first(matches: list[Finding], known: set[str], limit: int = FLAGGED_FILES) -> list[str]:
    """The files the scanner is most worried about, which are read whatever else is.

    A model choosing from names alone can talk itself out of a file a scanner matched
    on. It should not get to.
    """
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    order = sorted(matches, key=lambda one: rank.get(one.severity, 5))
    chosen: list[str] = []
    for one in order:
        if one.file in known and one.file not in chosen:
            chosen.append(one.file)
        if len(chosen) >= limit:
            break
    return chosen


def read_files(repository: Path, paths: list[str]) -> tuple[str, list[str]]:
    """The source of each file, numbered, and which ones could be read."""
    blocks: list[str] = []
    read: list[str] = []
    for path in paths:
        try:
            source = (repository / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not source.strip():
            continue
        blocks.append(f"=== {path} ===\n{numbered(source)}")
        read.append(path)
    return "\n\n".join(blocks), read


def _failed(
    store: Store, run: Run, log: Logger, reason: str, error: str, started: float
) -> AuditOutcome:
    run.status = "failed"
    run.reason = reason
    run.error = error
    run.duration_ms = int((time.monotonic() - started) * 1000)
    log.error("audit failed", reason=reason, error=error)
    return AuditOutcome(finish(store, run), [])


async def audit(
    store: Store,
    gateway: Gateway,
    repository: Repository,
    policy: Policy,
    log: Logger | None = None,
    sandbox: Sandbox | None = None,
    image: str = "",
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

    # --- Semgrep, as a place to look and nothing more ---------------------------------
    matches: list[Finding] = []
    if sandbox is not None and image:
        found = await asyncio.to_thread(scan, sandbox, str(repository.path), image, run.id, log=log)
        matches = found.findings
        if found.errors:
            log.warn("scan incomplete", reason="scan_errors", errors=found.errors[:3])
        log.info("scan finished", matched=len(matches), files=len({one.file for one in matches}))

    # --- pass one: which files are worth reading -------------------------------------
    try:
        choice = await gateway.complete(
            JobClass.TRIAGE,
            [
                Message(role="system", content=CHOOSE),
                Message(
                    role="user",
                    content=(
                        f"Repository: {repository.slug}{hints}\n\nOutline:\n{shape}"
                        + (
                            f"\n\nA static analyser flagged:\n{flagged}"
                            if (flagged := flagged_text(matches))
                            else ""
                        )
                    ),
                ),
            ],
            profile=policy.model_profile,
            response_format=as_response_format(CHOICE_SCHEMA),
        )
    except ModelError as error:
        return _failed(store, run, log, "model_failed", str(error), started)

    known = {
        str(row["path"])
        for row in store.query(
            "SELECT DISTINCT path FROM chunks WHERE repo_path = ?", (str(repository.path),)
        )
    }
    # The scanner's worst files go in whatever the model said, then the model's picks
    # fill the rest of the budget.
    forced = flagged_first(matches, known)
    chosen = forced + [
        path for path in wanted_files(choice.text, known, READ_FILES) if path not in forced
    ]
    chosen = chosen[:READ_FILES]
    if not chosen:
        run.status = "skipped"
        run.reason = "nothing_chosen"
        run.prompt_tokens = choice.prompt_tokens
        run.completion_tokens = choice.completion_tokens
        run.duration_ms = int((time.monotonic() - started) * 1000)
        log.info("audit skipped", reason="nothing_chosen", outline_bytes=len(shape))
        return AuditOutcome(finish(store, run), [], len(shape))

    source, read = read_files(repository.path, chosen)
    if not source:
        return _failed(store, run, log, "unreadable", f"could not read {chosen}", started)

    # --- pass two: review the code in them --------------------------------------------
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
            content=f"Repository: {repository.slug}{hints}\n\n{source}",
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
        return _failed(store, run, log, "model_failed", str(error), started)

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
            snippet=f"audit:{item.file.strip()}:{item.line}",
            run_id=run.id,
        )
        for item in raw
        # A finding about a file nobody read is a finding about nothing.
        if item.file.strip() in read
    ]
    record(store, findings)
    # Only the files this audit read are settled by it. Closing a finding in a file
    # this run never opened would clear it because nobody looked, which is not the same
    # as nobody finding it.
    closed = close_missing(
        store,
        repository.path,
        "audit",
        [finding.fingerprint for finding in findings],
        "the audit no longer reports it",
        files=read,
    )
    if closed:
        log.info("findings closed", count=closed, reason="not_reported")
    set_audited(store, repository.path)

    run.status = "ok"
    run.finding_count = len(findings)
    run.prompt_tokens = choice.prompt_tokens + completion.prompt_tokens
    run.completion_tokens = choice.completion_tokens + completion.completion_tokens
    run.backend = completion.backend
    run.duration_ms = int((time.monotonic() - started) * 1000)
    if problems:
        run.error = "; ".join(problems[:3])
    log.info(
        "audit finished",
        findings=len(findings),
        files_read=len(read),
        outline_bytes=len(shape),
    )
    return AuditOutcome(finish(store, run), findings, len(shape), problems, tuple(read))

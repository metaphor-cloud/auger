"""Review a commit range, or the work that is not committed yet.

This is the default job. Everything before it exists to make this one safe and cheap:
the policy decides whether it runs at all, the busy check decides when, the gateway
decides which model answers, and the fingerprint decides whether the result is new.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from reviewrig.config import Policy
from reviewrig.config.schema import CodeGraph as CodeGraphConfig
from reviewrig.config.schema import JobClass
from reviewrig.context import ReviewContext, context_for_diff, reindex
from reviewrig.jobs.parse import RawFinding, parse_findings
from reviewrig.jobs.prompt import review_messages
from reviewrig.jobs.tools import complete_with_tools
from reviewrig.llm import Gateway, ModelError
from reviewrig.log import Logger, create_logger
from reviewrig.mcp import McpRegistry
from reviewrig.models import Repository
from reviewrig.store import Store
from reviewrig.store.findings import Finding, record
from reviewrig.store.runs import Run, finish, set_reviewed_head, start
from reviewrig.watch import git

KIND = "diff_review"
#: How many diff lines around a finding go into its fingerprint.
SNIPPET_RADIUS = 2
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class ReviewOutcome:
    run: Run
    findings: list[Finding]
    problems: list[str]
    context: ReviewContext | None = None


def snippet_for(diff: str, file: str, line: int | None) -> str:
    """The lines of the diff that a finding points at.

    The fingerprint uses this instead of the line number, so a finding that moves down
    the file because an import was added above it stays the same finding.
    """
    lines = diff.splitlines()
    in_file = False
    current = 0
    collected: list[str] = []
    for entry in lines:
        if entry.startswith("+++ "):
            in_file = entry[4:].removeprefix("b/").strip() == file.strip()
            continue
        if not in_file:
            continue
        if entry.startswith("diff --git "):
            in_file = False
            continue
        match = HUNK.match(entry)
        if match:
            current = int(match.group(1))
            continue
        if entry.startswith("+") or entry.startswith(" "):
            if line is None or abs(current - line) <= SNIPPET_RADIUS:
                collected.append(entry[1:].strip())
            current += 1
        elif entry.startswith("-"):
            continue
    return " ".join(collected[:6])


def to_finding(raw: RawFinding, repository: Repository, diff_text: str, run_id: str) -> Finding:
    return Finding(
        repo_path=str(repository.path),
        source="model",
        severity=raw.severity,
        category=raw.category,
        title=raw.title.strip(),
        detail=raw.detail.strip(),
        suggestion=raw.suggestion.strip(),
        file=raw.file.strip(),
        line=raw.line,
        confidence=raw.confidence,
        snippet=snippet_for(diff_text, raw.file, raw.line),
        run_id=run_id,
    )


def collect_diff(repository: Path, base: str | None, target: str) -> tuple[str, str, str]:
    """Return the patch, the subject line, and the branch."""
    state = git.state(repository)
    if target == "WORKTREE":
        return git.working_tree_diff(repository), "uncommitted changes", state.branch
    subject = ""
    entries = git.commits(repository, limit=1)
    if entries:
        subject = entries[0].subject
    return git.diff(repository, base, target), subject, state.branch


async def review(
    store: Store,
    gateway: Gateway,
    repository: Repository,
    policy: Policy,
    base: str | None = None,
    target: str = "HEAD",
    tools: McpRegistry | None = None,
    graph: CodeGraphConfig | None = None,
    log: Logger | None = None,
) -> ReviewOutcome:
    """Review one change. Never raises: a failure is recorded on the run."""
    log = (log or create_logger("jobs")).bind(repo=repository.slug, kind=KIND)
    started = time.monotonic()
    head = target if target != "WORKTREE" else "WORKTREE"
    run = start(store, repository.path, KIND, base, head)
    log = log.bind(run=run.id)

    try:
        diff_text, subject, branch = collect_diff(repository.path, base, target)
    except git.GitError as error:
        return _failed(store, run, log, "git_failed", str(error), started)

    if not diff_text.strip():
        run.status = "skipped"
        run.reason = "empty_diff"
        run.duration_ms = int((time.monotonic() - started) * 1000)
        log.info("review skipped", reason="empty_diff")
        return ReviewOutcome(finish(store, run), [], [])

    # The index must match the code under review, so it is brought up to date first.
    # A commit that touched one file costs one file.
    await reindex(store, gateway, repository.path, policy.model_profile, log)
    context = await context_for_diff(
        store,
        gateway,
        str(repository.path),
        diff_text,
        policy.model_profile,
        graph=graph,
        log=log,
    )

    messages = review_messages(
        slug=repository.slug,
        branch=branch,
        head=head,
        subject=subject,
        diff=diff_text,
        hints=policy.hints,
        context=context.as_text(),
        instructions=policy.instructions,
    )
    try:
        completion, tool_run = await complete_with_tools(
            gateway, tools, JobClass.REVIEW, messages, policy, log
        )
    except ModelError as error:
        return _failed(store, run, log, "model_failed", str(error), started)

    raw_findings, problems = parse_findings(completion.text)
    findings = [to_finding(raw, repository, diff_text, run.id) for raw in raw_findings]
    record(store, findings)
    if target != "WORKTREE":
        set_reviewed_head(store, repository.path, git.head(repository.path))

    run.status = "ok"
    run.finding_count = len(findings)
    run.prompt_tokens = completion.prompt_tokens
    run.completion_tokens = completion.completion_tokens
    run.backend = completion.backend
    run.duration_ms = int((time.monotonic() - started) * 1000)
    if problems:
        run.error = "; ".join(problems[:3])
        log.warn("review answer partly unreadable", reason="bad_answer", problems=problems)
    log.info(
        "review finished",
        findings=len(findings),
        backend=completion.backend,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        context_chunks=len(context.hits),
        graph_chunks=context.graph_hits,
        reranked=context.reranked,
        tool_calls=tool_run.calls,
    )
    return ReviewOutcome(finish(store, run), findings, problems, context)


def _failed(
    store: Store, run: Run, log: Logger, reason: str, detail: str, started: float
) -> ReviewOutcome:
    run.status = "failed"
    run.reason = reason
    run.error = detail
    run.duration_ms = int((time.monotonic() - started) * 1000)
    log.error("review failed", reason=reason, error=detail)
    return ReviewOutcome(finish(store, run), [], [detail])

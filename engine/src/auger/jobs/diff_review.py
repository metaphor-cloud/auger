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

from auger.config import Policy
from auger.config.schema import CodeGraph as CodeGraphConfig
from auger.config.schema import JobClass
from auger.context import ReviewContext, context_for_diff, reindex
from auger.jobs.parse import (
    FINDINGS_SCHEMA,
    REPAIR,
    RawFinding,
    as_response_format,
    parse_findings,
)
from auger.jobs.prompt import review_messages
from auger.jobs.shell import Shell
from auger.jobs.tools import complete_with_tools
from auger.llm import Gateway, Message, ModelError
from auger.log import Logger, create_logger
from auger.mcp import McpRegistry
from auger.models import Repository
from auger.store import Store
from auger.store.findings import Finding, record
from auger.store.runs import Run, finish, set_reviewed_head, start
from auger.watch import git

KIND = "diff_review"
#: The shape the answer has to fit, held to by the decoder itself.
ANSWER_FORMAT = as_response_format(FINDINGS_SCHEMA)
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
    shell: Shell | None = None,
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
    # A commit that touched one file costs one file. Retrieval needs the embedding
    # model, so it fails the same way a review does when no model answers.
    try:
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
    except ModelError as error:
        return _failed(store, run, log, "model_failed", str(error), started)

    # What this model can actually hold. The related code is drawn to fill it, and the
    # whole prompt is cut to fit it, so a large diff loses context rather than the
    # review losing the server.
    budget = gateway.prompt_budget(JobClass.REVIEW, policy.model_profile)
    messages = review_messages(
        slug=repository.slug,
        branch=branch,
        head=head,
        subject=subject,
        diff=diff_text,
        hints=policy.hints,
        # Related code takes whatever the diff leaves, so a small change is reviewed
        # with a lot of it and a large one with less.
        context=context.as_text(budget=max(0, budget - len(diff_text))),
        instructions=policy.instructions,
        rules=policy.system_prompt,
        budget=budget,
    )
    try:
        completion, tool_run = await complete_with_tools(
            gateway,
            tools,
            JobClass.REVIEW,
            messages,
            policy,
            log,
            answer=ANSWER_FORMAT,
            shell=shell,
            budget=budget,
        )
    except ModelError as error:
        return _failed(store, run, log, "model_failed", str(error), started)

    raw_findings, problems = parse_findings(completion.text)
    if problems and not raw_findings:
        # A schema makes this rare rather than impossible: a model can still answer with
        # an empty object it fills badly. One more turn costs less than a lost review.
        try:
            repaired = await gateway.complete(
                JobClass.REVIEW,
                [
                    *messages,
                    Message(role="assistant", content=completion.text),
                    Message(role="user", content=REPAIR),
                ],
                profile=policy.model_profile,
                response_format=ANSWER_FORMAT,
            )
        except ModelError as error:
            log.warn("repair failed", reason="model_failed", error=error)
        else:
            second, still_bad = parse_findings(repaired.text)
            if second:
                log.info("answer repaired", findings=len(second), was=problems[:2])
                raw_findings, problems = second, still_bad
                completion = repaired
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

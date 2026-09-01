"""Review one pull request.

Two modes, and the difference matters to the people around the user.

`draft` writes a review that waits. On GitHub it is a pending review, on GitLab a set of
draft notes. Nobody but the user sees it until the user submits it.

`complete` submits. Comments appear on the pull request under the user's name, so the
mode is never the default and the UI names every repository that uses it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from auger.config import Policy
from auger.config.schema import JobClass
from auger.forge import Comment, Entry, ForgeError, PostedReview, PullRequest, Repo
from auger.jobs.diff_review import ANSWER_FORMAT
from auger.jobs.lookup import Lookup
from auger.jobs.parse import parse_findings
from auger.jobs.prompt import review_messages
from auger.jobs.shell import Shell
from auger.jobs.tools import complete_with_tools
from auger.llm import Gateway, ModelError
from auger.log import Logger, create_logger
from auger.mcp import McpRegistry
from auger.models import Repository
from auger.store import Store
from auger.store.findings import Finding, record
from auger.store.runs import Run, finish, start

KIND = "pr_review"
#: Findings below this confidence are summarised, not placed on a line. A wrong comment
#: on someone else's pull request costs more than a missed one.
COMMENT_CONFIDENCE = 0.6
SEVERITY_MARK = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Note",
}


@dataclass(frozen=True)
class PullOutcome:
    run: Run
    findings: list[Finding]
    posted: PostedReview | None = None
    problems: list[str] | None = None


def summary_text(findings: list[Finding], mode: str) -> str:
    """The body of the review. It says what the rig is and what it found."""
    if not findings:
        return "auger found nothing to report in this change."
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    parts = ", ".join(f"{count} {name}" for name, count in counts.items())
    lines = [f"auger found {len(findings)} things to look at: {parts}.", ""]
    for finding in findings:
        if finding.confidence < COMMENT_CONFIDENCE:
            where = f"{finding.file}:{finding.line}" if finding.line else finding.file
            lines.append(
                f"- **{SEVERITY_MARK.get(finding.severity, finding.severity)}** "
                f"`{where}` {finding.title} (low confidence)"
            )
    if mode == "draft":
        lines += ["", "_This review is a draft. Nobody else sees it until you submit it._"]
    return "\n".join(lines).strip()


def to_comments(findings: list[Finding]) -> list[Comment]:
    """A finding becomes a line comment when it is confident and it knows where it is."""
    comments: list[Comment] = []
    for finding in findings:
        if finding.line is None or finding.confidence < COMMENT_CONFIDENCE:
            continue
        body = f"**{SEVERITY_MARK.get(finding.severity, finding.severity)}: {finding.title}**\n\n"
        body += finding.detail
        if finding.suggestion:
            body += f"\n\n**Fix:** {finding.suggestion}"
        comments.append(Comment(path=finding.file, line=finding.line, body=body))
    return comments


async def review_pull(
    store: Store,
    gateway: Gateway,
    entry: Entry,
    repo: Repo,
    pull: PullRequest,
    repository: Repository,
    policy: Policy,
    tools: McpRegistry | None = None,
    log: Logger | None = None,
    shell: Shell | None = None,
) -> PullOutcome:
    """Review one pull request and post the result. Never raises."""
    log = (log or create_logger("jobs")).bind(repo=repository.slug, kind=KIND, pull=pull.number)
    started = time.monotonic()
    run = start(store, repository.path, KIND, pull.base_ref, pull.head_sha)
    log = log.bind(run=run.id)

    if policy.mode == "off":
        return _stop(store, run, log, "mode_off", "", started)

    try:
        diff = await entry.forge.diff(repo, pull.number)
    except ForgeError as error:
        return _stop(store, run, log, "forge_failed", str(error), started, failed=True)
    if not diff.strip():
        return _stop(store, run, log, "empty_diff", "", started)

    budget = gateway.prompt_budget(JobClass.REVIEW, policy.model_profile, policy.working_set_tokens)
    messages = review_messages(
        slug=repository.slug,
        branch=pull.base_ref,
        head=pull.head_sha,
        subject=f"pull request #{pull.number}: {pull.title}",
        diff=diff,
        hints=policy.hints,
        instructions=policy.instructions,
        rules=policy.system_prompt,
        budget=budget,
    )
    try:
        completion, _ = await complete_with_tools(
            gateway,
            tools,
            JobClass.REVIEW,
            messages,
            policy,
            log,
            answer=ANSWER_FORMAT,
            shell=shell,
            lookup=Lookup(store, repository.path) if policy.code_tools else None,
            budget=budget,
        )
    except ModelError as error:
        return _stop(store, run, log, "model_failed", str(error), started, failed=True)

    raw, problems = parse_findings(completion.text)
    findings = [
        Finding(
            repo_path=str(repository.path),
            source="model",
            severity=item.severity,
            category=item.category,
            title=item.title.strip(),
            detail=item.detail.strip(),
            suggestion=item.suggestion.strip(),
            file=item.file.strip(),
            line=item.line,
            confidence=item.confidence,
            snippet=f"pull-{pull.number}",
            run_id=run.id,
        )
        for item in raw
    ]
    record(store, findings)

    posted: PostedReview | None = None
    try:
        posted = await entry.forge.post_review(
            repo,
            pull,
            summary_text(findings, policy.mode),
            to_comments(findings),
            submit=policy.mode == "complete",
        )
    except ForgeError as error:
        run.error = str(error)
        log.error("review not posted", reason="post_failed", error=error)

    run.status = "ok"
    run.reason = policy.mode
    run.finding_count = len(findings)
    run.prompt_tokens = completion.prompt_tokens
    run.completion_tokens = completion.completion_tokens
    run.backend = completion.backend
    run.duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "pull request reviewed",
        findings=len(findings),
        mode=policy.mode,
        submitted=bool(posted and posted.submitted),
    )
    return PullOutcome(finish(store, run), findings, posted, problems)


def _stop(
    store: Store,
    run: Run,
    log: Logger,
    reason: str,
    detail: str,
    started: float,
    failed: bool = False,
) -> PullOutcome:
    run.status = "failed" if failed else "skipped"
    run.reason = reason
    run.error = detail or None
    run.duration_ms = int((time.monotonic() - started) * 1000)
    if failed:
        log.error("pull request review failed", reason=reason, error=detail)
    else:
        log.info("pull request review skipped", reason=reason)
    return PullOutcome(finish(store, run), [])

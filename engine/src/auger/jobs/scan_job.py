"""One security scan: Semgrep in the sandbox, then triage by the model.

Semgrep alone produces a list that nobody reads. The triage step is what makes it worth
running: it sends only the findings and their context, so the cost is a fraction of a
review, and it dismisses what the pattern matched but the code does not do.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from auger.config import Policy
from auger.jobs.semgrep import KIND, ScanOutcome, scan
from auger.jobs.triage import TriageOutcome, triage
from auger.llm import Gateway
from auger.log import Logger, create_logger
from auger.models import Repository
from auger.sandbox import Sandbox
from auger.store import Store
from auger.store.findings import Finding, close_missing, record
from auger.store.runs import Run, finish, start


@dataclass(frozen=True)
class ScanRunOutcome:
    run: Run
    findings: list[Finding]
    scan: ScanOutcome | None = None
    triage: TriageOutcome | None = None


async def run_scan(
    store: Store,
    gateway: Gateway,
    sandbox: Sandbox,
    repository: Repository,
    policy: Policy,
    image: str,
    log: Logger | None = None,
) -> ScanRunOutcome:
    """Scan one repository and judge what it found. Never raises."""
    log = (log or create_logger("jobs")).bind(repo=repository.slug, kind=KIND)
    started = time.monotonic()
    run = start(store, repository.path, KIND, None, None)
    log = log.bind(run=run.id)

    outcome = await asyncio.to_thread(scan, sandbox, str(repository.path), image, run.id, log=log)
    record(store, outcome.findings)
    # A scan reads the whole repository, so what it does not report this time is fixed,
    # or came from a rule that is no longer in the set. Either way it is not a finding
    # any more, and a list nobody can clear is a list nobody reads.
    closed = await asyncio.to_thread(
        close_missing,
        store,
        repository.path,
        "semgrep",
        [finding.fingerprint for finding in outcome.findings],
        "the scan no longer reports it",
    )
    if closed:
        log.info("findings closed", count=closed, reason="not_reported")

    judged: TriageOutcome | None = None
    if outcome.findings:
        judged = await triage(store, gateway, outcome.findings, policy, log)

    run.status = "failed" if outcome.errors and not outcome.findings else "ok"
    run.reason = "scan_failed" if run.status == "failed" else None
    run.error = "; ".join(outcome.errors[:3]) or None
    run.finding_count = len(outcome.findings)
    run.duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "scan run finished",
        status=run.status,
        found=len(outcome.findings),
        dismissed=judged.dismissed if judged else 0,
    )
    return ScanRunOutcome(finish(store, run), outcome.findings, outcome, judged)

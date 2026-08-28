from typing import Protocol

from auger.jobs import audit
from auger.jobs.audit import AuditOutcome
from auger.jobs.diff_review import KIND, ReviewOutcome, review
from auger.jobs.pr_review import PullOutcome, review_pull
from auger.jobs.scan_job import ScanRunOutcome, run_scan
from auger.jobs.triage import TriageOutcome, triage
from auger.store.findings import Finding
from auger.store.runs import Run


class JobOutcome(Protocol):
    """What every job returns: a run row, and whatever it found."""

    @property
    def run(self) -> Run: ...

    @property
    def findings(self) -> list[Finding]: ...


__all__ = [
    "KIND",
    "AuditOutcome",
    "JobOutcome",
    "PullOutcome",
    "ReviewOutcome",
    "ScanRunOutcome",
    "TriageOutcome",
    "audit",
    "review",
    "review_pull",
    "run_scan",
    "triage",
]

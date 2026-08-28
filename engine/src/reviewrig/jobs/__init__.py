from typing import Protocol

from reviewrig.jobs.diff_review import KIND, ReviewOutcome, review
from reviewrig.jobs.pr_review import PullOutcome, review_pull
from reviewrig.jobs.scan_job import ScanRunOutcome, run_scan
from reviewrig.jobs.triage import TriageOutcome, triage
from reviewrig.store.findings import Finding
from reviewrig.store.runs import Run


class JobOutcome(Protocol):
    """What every job returns: a run row, and whatever it found."""

    @property
    def run(self) -> Run: ...

    @property
    def findings(self) -> list[Finding]: ...


__all__ = [
    "KIND",
    "JobOutcome",
    "PullOutcome",
    "ReviewOutcome",
    "ScanRunOutcome",
    "TriageOutcome",
    "review",
    "review_pull",
    "run_scan",
    "triage",
]

"""Static analysis in the sandbox.

Semgrep reads the repository and runs its own rules, and a rule is code. It therefore
runs where every analysis step runs: in a container, with the repository read only, with
no network, and with a time limit.

Its rules are vendored into the analysis image, so a scan needs no fetch at run time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from reviewrig.log import Logger, create_logger
from reviewrig.sandbox import WORK, Network, RunResult, RunSpec, Sandbox, SandboxError
from reviewrig.store.findings import Finding, Severity

KIND = "security_scan"
#: Where `just build-image` puts the rules. A scan with no network cannot fetch them.
DEFAULT_RULES = "/opt/semgrep-rules"
DEFAULT_TIMEOUT = 900.0

#: Semgrep speaks in three levels. The rig speaks in five.
SEVERITY: dict[str, Severity] = {
    "ERROR": "high",
    "WARNING": "medium",
    "INFO": "low",
}
CONFIDENCE: dict[str, float] = {"HIGH": 0.8, "MEDIUM": 0.6, "LOW": 0.4}


@dataclass(frozen=True)
class ScanOutcome:
    findings: list[Finding]
    errors: list[str]
    result: RunResult | None = None


def command(rules: str = DEFAULT_RULES) -> list[str]:
    """The scan command. Kept pure, so a test can read every flag."""
    return [
        "semgrep",
        "scan",
        "--json",
        "--quiet",
        "--config",
        rules,
        # The rules are local, so nothing may reach out.
        "--metrics",
        "off",
        "--disable-version-check",
        # Scratch is the only writable place in the sandbox.
        "--use-git-ignore",
        WORK,
    ]


def severity_of(entry: dict[str, Any]) -> Severity:
    extra = entry.get("extra") or {}
    metadata = extra.get("metadata") or {}
    level = SEVERITY.get(str(extra.get("severity", "")).upper(), "medium")
    # A rule that names both a high impact and a high likelihood is the worst kind.
    if level == "high" and str(metadata.get("impact", "")).upper() == "HIGH":
        return "critical"
    return level


def confidence_of(entry: dict[str, Any]) -> float:
    metadata = (entry.get("extra") or {}).get("metadata") or {}
    return CONFIDENCE.get(str(metadata.get("confidence", "")).upper(), 0.5)


def to_finding(entry: dict[str, Any], repo_path: str, run_id: str) -> Finding | None:
    """One Semgrep result becomes one finding, or None when it names no file."""
    path = str(entry.get("path", "")).removeprefix(f"{WORK}/").strip()
    check = str(entry.get("check_id", "")).strip()
    if not path or not check:
        return None
    extra = entry.get("extra") or {}
    lines = str(extra.get("lines", "")).strip()
    return Finding(
        repo_path=repo_path,
        source="semgrep",
        severity=severity_of(entry),
        title=check.rsplit(".", 1)[-1].replace("-", " "),
        detail=str(extra.get("message", "")).strip(),
        suggestion=str(extra.get("fix", "") or "").strip(),
        file=path,
        line=int((entry.get("start") or {}).get("line", 0)) or None,
        confidence=confidence_of(entry),
        snippet=f"{check}\n{lines}",
        run_id=run_id,
    )


def parse(output: str, repo_path: str, run_id: str) -> ScanOutcome:
    """Read Semgrep's JSON. A scan that reported nothing usable is not a failure."""
    try:
        body = json.loads(output or "{}")
    except json.JSONDecodeError as error:
        return ScanOutcome([], [f"semgrep output was not JSON: {error}"])
    findings = [
        finding
        for entry in body.get("results", [])
        if isinstance(entry, dict) and (finding := to_finding(entry, repo_path, run_id))
    ]
    errors = [
        str(item.get("message", item)) for item in body.get("errors", []) if isinstance(item, dict)
    ]
    return ScanOutcome(findings, errors)


def scan(
    sandbox: Sandbox,
    repository_path: str,
    image: str,
    run_id: str,
    rules: str = DEFAULT_RULES,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    log: Logger | None = None,
) -> ScanOutcome:
    """Run Semgrep over one repository. Never raises."""
    from pathlib import Path

    log = (log or create_logger("jobs")).bind(component="semgrep")
    spec = RunSpec(
        repository=Path(repository_path),
        command=command(rules),
        image=image,
        timeout_seconds=timeout_seconds,
        network=Network.NONE,
    )
    try:
        result = sandbox.run(spec)
    except SandboxError as error:
        log.warn("scan could not start", reason="sandbox_failed", error=error)
        return ScanOutcome([], [str(error)])
    if result.timed_out:
        log.warn("scan timed out", reason="timeout", seconds=timeout_seconds)
        return ScanOutcome([], ["the scan passed its time limit"], result)
    outcome = parse(result.stdout, repository_path, run_id)
    if not outcome.findings and result.exit_code not in (0, 1):
        # Semgrep exits 1 when it found something. Anything else with no findings failed.
        return ScanOutcome(
            [], [*outcome.errors, result.stderr.strip()[:400] or "semgrep failed"], result
        )
    log.info(
        "scan finished",
        findings=len(outcome.findings),
        errors=len(outcome.errors),
        seconds=round(result.duration_seconds, 1),
    )
    return ScanOutcome(outcome.findings, outcome.errors, result)

"""Response bodies. The UI depends on these names, so keep them stable."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from reviewrig.config import Policy
from reviewrig.models import RepositoryView
from reviewrig.store.findings import Finding
from reviewrig.store.runs import Run


class RepositoryOut(BaseModel):
    path: str
    name: str
    slug: str
    host: str | None
    namespace: str | None
    org_key: str | None
    policy: Policy

    @classmethod
    def of(cls, view: RepositoryView) -> RepositoryOut:
        remote = view.repository.remote
        return cls(
            path=str(view.repository.path),
            name=view.repository.name,
            slug=view.repository.slug,
            host=remote.host if remote else None,
            namespace=remote.namespace if remote else None,
            org_key=view.repository.org_key,
            policy=view.policy,
        )


class RepositoryList(BaseModel):
    repositories: list[RepositoryOut]
    enabled: int

    @classmethod
    def of(cls, views: list[RepositoryView]) -> RepositoryList:
        return cls(
            repositories=[RepositoryOut.of(view) for view in views],
            enabled=sum(1 for view in views if view.policy.enabled),
        )


class SandboxOut(BaseModel):
    backend: str
    degraded: bool
    warning: str | None


class EgressOut(BaseModel):
    proxy_url: str
    allowed: list[str]
    allowed_requests: int
    refused_requests: int
    failed_requests: int
    recently_refused: list[str]


class IndexOut(BaseModel):
    files: int
    chunks: int
    vectors: bool
    embedded: int


class SystemOut(BaseModel):
    """What the UI shows about the rig itself, including what it must warn about."""

    version: str
    sandbox: SandboxOut
    egress: EgressOut
    index: IndexOut
    image: str


class BackendOut(BaseModel):
    name: str
    url: str
    model: str
    up: bool
    hosted: bool
    managed: bool
    models_served: list[str]
    reason: str | None
    requests: int
    prompt_tokens: int
    completion_tokens: int
    failures: int


class BackendList(BaseModel):
    backends: list[BackendOut]
    profiles: dict[str, dict[str, str]]
    active_profile_backends: dict[str, str]
    allow_hosted: bool


class FindingOut(BaseModel):
    fingerprint: str
    repo_path: str
    source: str
    severity: str
    title: str
    detail: str
    suggestion: str
    file: str
    line: int | None
    confidence: float
    status: str
    triage: str | None
    first_seen_at: str
    last_seen_at: str
    times_seen: int
    run_id: str | None

    @classmethod
    def of(cls, finding: Finding) -> FindingOut:
        return cls(**{name: getattr(finding, name) for name in cls.model_fields})


class FindingList(BaseModel):
    findings: list[FindingOut]
    counts: dict[str, int]


class RunOut(BaseModel):
    id: str
    repo_path: str
    kind: str
    status: str
    reason: str | None
    base: str | None
    head: str | None
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    finding_count: int
    prompt_tokens: int
    completion_tokens: int
    backend: str | None
    error: str | None
    attempts: int

    @classmethod
    def of(cls, run: Run) -> RunOut:
        return cls(**{name: getattr(run, name) for name in cls.model_fields})


class RunList(BaseModel):
    runs: list[RunOut]


class QueueOut(BaseModel):
    pending: int
    in_flight: list[str]
    paused: bool
    workers: int


class ReviewRequest(BaseModel):
    path: str
    base: str | None = None
    #: `HEAD` reviews the last commit. `WORKTREE` reviews what is not committed yet.
    target: str = "HEAD"


class StatusRequest(BaseModel):
    fingerprints: list[str]
    status: Literal["open", "suppressed", "resolved"]


class ForgeOut(BaseModel):
    name: str
    kind: str
    host: str
    enabled: bool
    reachable: bool
    user: str
    reason: str | None


class ForgeList(BaseModel):
    forges: list[ForgeOut]


class PolicyLevelOut(BaseModel):
    """One row of the settings table: a level, its key, and what it sets."""

    level: Literal["defaults", "org", "repo"]
    key: str
    overrides: dict[str, object]


class SettingsOut(BaseModel):
    defaults: Policy
    levels: list[PolicyLevelOut]
    config_path: str


class PolicyChange(BaseModel):
    level: Literal["defaults", "org", "repo"]
    #: Empty for `defaults`. An organisation key or a repository path otherwise.
    key: str = ""
    changes: dict[str, object]


class ToolOut(BaseModel):
    server: str
    name: str
    qualified: str
    description: str


class McpServerOut(BaseModel):
    name: str
    transport: str
    target: str
    reachable: bool
    reason: str | None
    tools: list[ToolOut]


class ToolList(BaseModel):
    servers: list[McpServerOut]
    #: What the default policy allows. Empty means no tool runs by default.
    allowed: list[str]

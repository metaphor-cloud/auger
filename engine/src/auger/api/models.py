"""Response bodies. The UI depends on these names, so keep them stable."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from auger.config import Policy
from auger.models import RepositoryView
from auger.store.findings import Finding
from auger.store.runs import Run


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
    #: Why the config file was refused, if it was.
    config_error: str | None = None


class BackendOut(BaseModel):
    name: str
    url: str
    model: str
    up: bool
    hosted: bool
    managed: bool
    #: This process started it, so this process can stop it. A server the user started
    #: themselves stays theirs, and the window offers no control that would do nothing.
    ours: bool = False
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
    category: str
    #: When the user first read it. Null means new to them, and the map says so.
    opened_at: str | None
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
    #: Whether a model can answer a review. False means pressing play would only
    #: produce one failed run per repository.
    models_ready: bool = False
    #: Why not, in the words the user needs to act on.
    models_reason: str | None = None


class ReviewRequest(BaseModel):
    path: str
    base: str | None = None
    #: `HEAD` reviews the last commit. `WORKTREE` reviews what is not committed yet.
    target: str = "HEAD"


class StatusRequest(BaseModel):
    fingerprints: list[str]
    status: Literal["open", "doing", "resolved", "suppressed"]


class RecordRequest(BaseModel):
    """One work item, written by a person rather than by a review."""

    repo_path: str
    title: str
    detail: str = ""
    file: str = ""
    line: int | None = None
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"


class RecordedOut(BaseModel):
    item: FindingOut
    #: True when the same work was already recorded. The window says so.
    existed: bool


class NoteOut(BaseModel):
    id: int
    author: str
    written_at: str
    text: str


class NoteList(BaseModel):
    notes: list[NoteOut]


class NoteRequest(BaseModel):
    text: str


class TurnOut(BaseModel):
    """One exchange with a model."""

    id: int
    at: float
    backend: str
    model: str
    job_class: str
    repo: str
    prompt: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    error: str | None
    clipped: bool


class TranscriptOut(BaseModel):
    turns: list[TurnOut]
    #: The highest id so far. Ask for what came after it to follow along.
    latest: int
    depth: int


class OnboardingOut(BaseModel):
    """What the first run still has to settle."""

    done: bool
    roots: int
    models_ready: bool
    repositories: int
    sandbox: str
    degraded: bool


class OnboardingChange(BaseModel):
    done: bool


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


class RootOut(BaseModel):
    path: str
    exclude: list[str]
    max_depth: int | None


class McpServerSetting(BaseModel):
    name: str
    transport: str
    target: str
    enabled: bool


class ForgeSetting(BaseModel):
    name: str
    host: str
    enabled: bool


class SettingsOut(BaseModel):
    defaults: Policy
    levels: list[PolicyLevelOut]
    config_path: str
    exclude: list[str]
    codegraph: bool
    codegraph_available: bool
    roots: list[RootOut]
    mcp: list[McpServerSetting]
    forges: list[ForgeSetting]
    schedule: dict[str, object]
    allow_hosted: bool


class SettingChange(BaseModel):
    """One setting, named by its dotted path.

    A key that holds a dot, such as a repository path or a forge host, is quoted:
    `repo."~/git/thing".priority`.
    """

    path: str
    value: object = None
    remove: bool = False


class ConfigText(BaseModel):
    text: str


class ExcludeChange(BaseModel):
    """Add or drop one entry of the exclusion list."""

    pattern: str
    remove: bool = False


class CodeGraphChange(BaseModel):
    enabled: bool


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
    #: This server asks for OAuth, so it needs a sign in the user starts.
    needs_sign_in: bool = False
    #: A token is stored. It does not promise the token still works.
    signed_in: bool = False


class ToolList(BaseModel):
    servers: list[McpServerOut]
    #: What the default policy allows. Empty means no tool runs by default.
    allowed: list[str]


class ModelChoiceOut(BaseModel):
    name: str
    job_class: str
    repo: str
    filename: str
    memory_gb: float
    description: str
    fits: bool
    #: Already on disk. The UI shows this so nobody waits for a download twice.
    downloaded: bool = False


class CatalogOut(BaseModel):
    """What the rig can fetch for itself, and what this machine can hold."""

    models: list[ModelChoiceOut]
    recommended: str
    usable_memory_gb: float
    runtime_installed: bool
    setup_running: bool


class SetupRequest(BaseModel):
    #: Empty means the model the rig recommends for this machine.
    model: str = ""
    #: Empty means the embedding model the rig recommends.
    embed: str = ""


class SetupOut(BaseModel):
    ok: bool
    review_model: str
    embed_model: str
    rerank_model: str
    runtime_path: str
    error: str | None


class RepositorySummaryOut(BaseModel):
    path: str
    name: str
    open_findings: int
    worst_severity: str
    last_run_at: str | None
    last_status: str | None


class DashboardOut(BaseModel):
    """Everything the landing page shows, read in one pass."""

    version: str
    #: What the rig is doing now.
    paused: bool
    pending: int
    in_flight: list[str]
    workers: int
    #: What it can do.
    sandbox: SandboxOut
    models_up: int
    models_total: int
    codegraph: bool
    #: What it is watching.
    repositories: int
    enabled: int
    excluded: int
    indexed_files: int
    chunks: int
    #: What it found.
    findings: dict[str, int]
    suppressed: int
    dismissed: int
    #: What it did.
    runs_today: int
    runs_by_status: dict[str, int]
    prompt_tokens: int
    completion_tokens: int
    last_run_at: str | None
    skipped_reasons: dict[str, int]
    busiest: list[RepositorySummaryOut]
    #: What needs the user.
    warnings: list[str]

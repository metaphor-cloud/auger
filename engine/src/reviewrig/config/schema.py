"""The shape of `~/.reviewrig/config.toml`.

Settings merge in three levels: the defaults, then a forge organisation, then one
repository. Every level after the first holds optional fields only, so an unset field
falls through to the level above it.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["off", "draft", "complete"]

#: Directories that no review ever needs. A user root such as `~` would otherwise take
#: minutes to walk and would find hundreds of dependency checkouts.
DEFAULT_EXCLUDE = (
    "**/node_modules/",
    "**/.venv/",
    "**/venv/",
    "**/vendor/",
    "**/target/",
    "**/.cargo/",
    "**/Library/",
    "**/.Trash/",
    "**/.cache/",
)

Priority = Annotated[int, Field(ge=1, le=9)]


def expand(path: Path | str) -> Path:
    """Expand `~` and make the path absolute, without a call to the file system."""
    return Path(path).expanduser().absolute()


class Root(BaseModel):
    """One directory tree to search for git repositories."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    exclude: list[str] = Field(default_factory=list)
    #: How deep to walk below the root. None means no limit.
    max_depth: int | None = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return expand(value)


class Policy(BaseModel):
    """A fully resolved setting set for one repository. Every field has a value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    mode: Mode = "draft"
    auto_review_assigned_prs: bool = True
    #: Wait this long after another agent stops before a review starts.
    idle_seconds: int = Field(default=300, ge=0)
    priority: Priority = 5
    model_profile: str = "balanced"
    #: Free text that tells the reviewer what matters in this repository.
    hints: str = ""
    #: MCP tools a job may call, as `server.tool` or `server.*`. Empty means none.
    tools: list[str] = Field(default_factory=list)
    #: How many tool calls one review may make.
    max_tool_calls: int = Field(default=8, ge=0, le=64)
    #: Run a whole repository audit this often. 0 turns audits off.
    audit_hours: int = Field(default=24, ge=0)


class Overrides(BaseModel):
    """A partial setting set. An unset field falls through to the level above."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    mode: Mode | None = None
    auto_review_assigned_prs: bool | None = None
    idle_seconds: int | None = Field(default=None, ge=0)
    priority: Priority | None = None
    model_profile: str | None = None
    hints: str | None = None
    tools: list[str] | None = None
    max_tool_calls: int | None = Field(default=None, ge=0, le=64)
    audit_hours: int | None = Field(default=None, ge=0)


class JobClass(StrEnum):
    """What a step asks the model for.

    A job never names a model. It names one of these, and the profile decides which
    backend answers. That indirection is the whole reason a model is easy to change.
    """

    TRIAGE = "triage"
    REVIEW = "review"
    EMBED = "embed"
    RERANK = "rerank"


class Backend(BaseModel):
    """One OpenAI-compatible server."""

    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:8080/v1"
    model: str = ""
    #: Name of the environment variable that holds the key. Never the key itself.
    api_key_env: str | None = None
    #: A continuous batch server stays full at this depth and is never over-committed.
    max_concurrent: int = Field(default=4, ge=1, le=64)
    #: True when the request leaves this machine. Off unless the user turns it on.
    hosted: bool = False
    #: Start this server if nothing answers at `url`.
    managed: bool = False
    #: Where the weights come from, for a managed server.
    model_file: str = ""
    model_url: str = ""
    #: Extra arguments for the managed server process.
    args: list[str] = Field(default_factory=list)


class ProfileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str
    max_tokens: int = Field(default=4096, ge=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)


class Profile(BaseModel):
    """One entry per job class."""

    model_config = ConfigDict(extra="forbid")

    triage: ProfileEntry = ProfileEntry(backend="local-triage", max_tokens=2048)
    review: ProfileEntry = ProfileEntry(backend="local-review", max_tokens=8192)
    embed: ProfileEntry = ProfileEntry(backend="local-embed", max_tokens=512)
    rerank: ProfileEntry = ProfileEntry(backend="local-rerank", max_tokens=512)

    def entry(self, job_class: JobClass) -> ProfileEntry:
        return getattr(self, job_class.value)  # type: ignore[no-any-return]


#: What the rig uses when the user has written no backend of their own. Every one is a
#: local server. `gpt-oss-120b` in its native MXFP4 form needs about 63 GB, which fits
#: in the unified memory of a workstation. The Q8 form needs about 120 GB and does not.
DEFAULT_BACKENDS: dict[str, Backend] = {
    "local-review": Backend(
        url="http://127.0.0.1:8080/v1",
        model="gpt-oss-120b",
        managed=True,
        model_file="gpt-oss-120b-mxfp4.gguf",
    ),
    "local-triage": Backend(
        url="http://127.0.0.1:8081/v1",
        model="qwen3-30b-a3b",
        managed=True,
        model_file="qwen3-30b-a3b-q4_k_m.gguf",
        max_concurrent=8,
    ),
    "local-embed": Backend(
        url="http://127.0.0.1:8082/v1",
        model="Qwen3-Embedding-0.6B",
        managed=True,
        model_file="qwen3-embedding-0.6b-f16.gguf",
        max_concurrent=8,
        args=["--embedding", "--pooling", "last"],
    ),
    "local-rerank": Backend(
        url="http://127.0.0.1:8083/v1",
        model="Qwen3-Reranker-0.6B",
        managed=True,
        model_file="qwen3-reranker-0.6b-f16.gguf",
        max_concurrent=8,
        args=["--reranking"],
    ),
}


class Forge(BaseModel):
    """One code forge the rig may read and comment on."""

    model_config = ConfigDict(extra="forbid")

    #: Off until the user turns it on. An enabled forge joins the egress allowlist.
    enabled: bool = False
    kind: Literal["github", "gitlab"] = "github"
    #: The host that appears in a git remote, which is how a repository is matched.
    host: str = "github.com"
    #: Where the API lives. A self hosted forge changes both of these.
    api: str = "https://api.github.com"
    #: Name of the environment variable that holds the token. Never the token itself.
    token_env: str = "GITHUB_TOKEN"
    #: A command line tool that can print a token, used when the variable is unset.
    token_command: list[str] = Field(default_factory=lambda: ["gh", "auth", "token"])


DEFAULT_FORGES: dict[str, Forge] = {
    "github": Forge(),
    "gitlab": Forge(
        kind="gitlab",
        host="gitlab.com",
        api="https://gitlab.com/api/v4",
        token_env="GITLAB_TOKEN",
        token_command=["glab", "auth", "token"],
    ),
}


class McpServer(BaseModel):
    """An MCP server the user attached.

    A server runs outside the sandbox and speaks for the user, so it is a trust
    boundary. Nothing is attached by default, and nothing it returns is treated as an
    instruction.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    transport: Literal["stdio", "http"] = "stdio"
    #: For `stdio`.
    command: str = ""
    args: list[str] = Field(default_factory=list)
    #: Environment for the server process. Names of variables to pass through, not values.
    pass_env: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    #: For `http`.
    url: str = ""
    #: How long one tool call may take.
    timeout_seconds: float = Field(default=30.0, gt=0)


class Schedule(BaseModel):
    """How hard the rig works."""

    model_config = ConfigDict(extra="forbid")

    #: Reviews that may run at once, across every repository.
    max_concurrent_reviews: int = Field(default=2, ge=1, le=16)
    #: How often the watcher looks for a new commit.
    poll_seconds: int = Field(default=60, ge=5)
    #: How often the watcher asks the forges for new pull requests. A forge counts
    #: requests, so this is slower than the local poll.
    forge_poll_seconds: int = Field(default=300, ge=30)
    #: How long to wait before trying a repository that was busy.
    retry_seconds: int = Field(default=120, ge=5)


class Egress(BaseModel):
    """Which destinations the rig may reach.

    A sandboxed step has no network at all, so this governs the engine and the
    subprocesses it starts. Model backends and enabled forges add themselves, so this
    list only holds what a user adds by hand.
    """

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    #: A hosted backend sends the user's code off the machine. It takes two switches:
    #: `hosted = true` on the backend, and this one. Neither alone is enough.
    allow_hosted: bool = False


def _copy[Model: BaseModel](defaults: dict[str, Model]) -> dict[str, Model]:
    """A deep copy per config.

    A shallow copy shares the model objects, so turning a forge on for one config would
    turn it on for every config built afterwards, including the next one a test builds.
    """
    return {name: value.model_copy(deep=True) for name, value in defaults.items()}


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roots: list[Root] = Field(default_factory=list)
    egress: Egress = Field(default_factory=Egress)
    schedule: Schedule = Field(default_factory=Schedule)
    forge: dict[str, Forge] = Field(default_factory=lambda: _copy(DEFAULT_FORGES))
    mcp: dict[str, McpServer] = Field(default_factory=dict)
    defaults: Policy = Field(default_factory=Policy)
    #: The image that every sandboxed step runs in.
    image: str = "reviewrig/analysis:0.1"
    backend: dict[str, Backend] = Field(default_factory=lambda: _copy(DEFAULT_BACKENDS))
    profile: dict[str, Profile] = Field(default_factory=lambda: {"balanced": Profile()})
    #: Keyed by `host/namespace`, for example `github.com/acme`. A shorter key matches
    #: every repository below it, so `github.com` covers a whole forge.
    org: dict[str, Overrides] = Field(default_factory=dict)
    #: Keyed by a repository path. A key may be an exact path or a glob.
    repo: dict[str, Overrides] = Field(default_factory=dict)

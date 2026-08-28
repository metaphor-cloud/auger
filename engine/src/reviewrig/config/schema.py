"""The shape of `~/.reviewrig/config.toml`.

Settings merge in three levels: the defaults, then a forge organisation, then one
repository. Every level after the first holds optional fields only, so an unset field
falls through to the level above it.
"""

from __future__ import annotations

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
    #: Names of MCP tools that a job may call. Empty means no tool.
    tools: list[str] = Field(default_factory=list)
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
    audit_hours: int | None = Field(default=None, ge=0)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roots: list[Root] = Field(default_factory=list)
    defaults: Policy = Field(default_factory=Policy)
    #: Keyed by `host/namespace`, for example `github.com/acme`. A shorter key matches
    #: every repository below it, so `github.com` covers a whole forge.
    org: dict[str, Overrides] = Field(default_factory=dict)
    #: Keyed by a repository path. A key may be an exact path or a glob.
    repo: dict[str, Overrides] = Field(default_factory=dict)

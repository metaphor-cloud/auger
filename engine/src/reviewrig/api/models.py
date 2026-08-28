"""Response bodies. The UI depends on these names, so keep them stable."""

from __future__ import annotations

from pydantic import BaseModel

from reviewrig.config import Policy
from reviewrig.rig import RepositoryView


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


class SystemOut(BaseModel):
    """What the UI shows about the rig itself, including what it must warn about."""

    version: str
    sandbox: SandboxOut
    egress: EgressOut
    image: str

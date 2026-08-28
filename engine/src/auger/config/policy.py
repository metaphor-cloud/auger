"""Resolve the three setting levels into one `Policy`.

Every other component reads the resolved `Policy` and never the raw config, so there is
one place to reason about which level wins.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from pathlib import Path

from auger.config.schema import Config, Overrides, Policy, expand
from auger.models import Repository


def matching_org_keys(keys: Mapping[str, object], org_key: str | None) -> list[str]:
    """Return every configured organisation key that covers `org_key`, broadest first.

    A broad key such as `github.com` and a narrow key such as `github.com/acme` both
    apply, and the narrow one wins where they disagree. A key only matches on a segment
    boundary, so `github.com/ac` never matches `github.com/acme`.
    """
    if org_key is None:
        return []
    segments = org_key.split("/")
    prefixes = {"/".join(segments[: index + 1]) for index in range(len(segments))}
    return sorted(prefixes & set(keys), key=lambda key: key.count("/"))


def matching_repo_keys(keys: Mapping[str, object], path: Path) -> list[str]:
    """Return every configured repository key that covers `path`, least specific first.

    A glob applies first, then an exact path, so an exact entry always wins.
    """
    target = str(path)
    exact = [key for key in keys if str(expand(key)) == target]
    globs = [
        key for key in keys if key not in exact and fnmatch.fnmatchcase(target, str(expand(key)))
    ]
    return sorted(globs, key=len) + exact


def apply(policy: Policy, overrides: Overrides) -> Policy:
    """Return `policy` with every field that `overrides` sets replaced."""
    changes = overrides.model_dump(exclude_none=True)
    return policy.model_copy(update=changes) if changes else policy


def resolve_policy(repository: Repository, config: Config) -> Policy:
    """Merge the defaults, then the organisation, then the repository.

    An excluded repository resolves to a policy that does nothing, so every caller sees
    the exclusion without having to check for it.
    """
    if is_excluded(repository, config) is not None:
        return config.defaults.model_copy(update={"enabled": False, "mode": "off"})
    policy = config.defaults
    for key in matching_org_keys(config.org, repository.org_key):
        policy = apply(policy, config.org[key])
    for key in matching_repo_keys(config.repo, repository.path):
        policy = apply(policy, config.repo[key])
    return policy


def is_excluded(repository: Repository, config: Config) -> str | None:
    """The pattern that excludes this repository, or None.

    A path, a glob, or a forge key. A forge key matches on a segment boundary, the same
    way an `[org]` section does, so `github.com/acme` never matches `github.com/acmecorp`.
    """
    target = str(repository.path)
    for pattern in config.exclude:
        text = pattern.strip()
        if not text:
            continue
        if str(expand(text)) == target or fnmatch.fnmatchcase(target, str(expand(text))):
            return pattern
        if repository.org_key and text in matching_org_keys({text: None}, repository.org_key):
            return pattern
        if repository.remote and text == repository.remote.slug:
            return pattern
    return None

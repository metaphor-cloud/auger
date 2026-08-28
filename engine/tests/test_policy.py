from __future__ import annotations

from pathlib import Path

from auger.config import Config, resolve_policy
from auger.config.policy import matching_org_keys, matching_repo_keys
from auger.models import Remote, Repository


def repository(path: str, slug: str | None = "github.com/acme/thing") -> Repository:
    remote = None
    if slug:
        host, namespace, name = slug.split("/", 2)
        remote = Remote(host=host, namespace=namespace, name=name)
    return Repository(path=Path(path), remote=remote)


def test_an_unset_field_keeps_the_default() -> None:
    config = Config.model_validate({"defaults": {"priority": 2}, "org": {"github.com": {}}})
    assert resolve_policy(repository("/x/thing"), config).priority == 2


def test_an_organisation_overrides_the_default() -> None:
    config = Config.model_validate(
        {"defaults": {"mode": "draft"}, "org": {"github.com/acme": {"mode": "complete"}}}
    )
    assert resolve_policy(repository("/x/thing"), config).mode == "complete"


def test_a_repository_overrides_its_organisation() -> None:
    config = Config.model_validate(
        {
            "defaults": {"mode": "draft"},
            "org": {"github.com/acme": {"mode": "complete", "priority": 3}},
            "repo": {"/x/thing": {"mode": "off"}},
        }
    )
    policy = resolve_policy(repository("/x/thing"), config)
    assert policy.mode == "off"
    # The organisation still supplies what the repository did not set.
    assert policy.priority == 3


def test_a_narrow_organisation_key_beats_a_broad_one() -> None:
    config = Config.model_validate(
        {
            "org": {
                "github.com": {"mode": "off", "priority": 9},
                "github.com/acme": {"mode": "complete"},
            }
        }
    )
    policy = resolve_policy(repository("/x/thing"), config)
    assert policy.mode == "complete"
    assert policy.priority == 9


def test_a_repository_with_no_remote_gets_no_organisation_settings() -> None:
    config = Config.model_validate({"org": {"github.com": {"mode": "complete"}}})
    assert resolve_policy(repository("/x/thing", slug=None), config).mode == "draft"


def test_an_exact_repository_key_beats_a_glob() -> None:
    config = Config.model_validate(
        {"repo": {"/x/*": {"priority": 8, "mode": "off"}, "/x/thing": {"priority": 1}}}
    )
    policy = resolve_policy(repository("/x/thing"), config)
    assert policy.priority == 1
    assert policy.mode == "off"


def test_hints_reach_the_policy() -> None:
    config = Config.model_validate({"repo": {"/x/thing": {"hints": "Ignore style."}}})
    assert resolve_policy(repository("/x/thing"), config).hints == "Ignore style."


def test_organisation_keys_match_on_a_segment_boundary() -> None:
    keys: dict[str, object] = {"github.com/ac": {}, "github.com/acme": {}, "gitlab.com": {}}
    assert matching_org_keys(keys, "github.com/acme") == ["github.com/acme"]


def test_organisation_keys_return_broadest_first() -> None:
    keys: dict[str, object] = {
        "github.com/acme/team": {},
        "github.com": {},
        "github.com/acme": {},
    }
    assert matching_org_keys(keys, "github.com/acme/team") == [
        "github.com",
        "github.com/acme",
        "github.com/acme/team",
    ]


def test_repository_keys_expand_a_home_prefix() -> None:
    target = Path.home() / "git" / "thing"
    assert matching_repo_keys({"~/git/thing": {}}, target) == ["~/git/thing"]


def test_a_repository_key_that_matches_nothing_is_ignored() -> None:
    assert matching_repo_keys({"/other/*": {}}, Path("/x/thing")) == []

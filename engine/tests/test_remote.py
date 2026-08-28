from __future__ import annotations

import pytest

from auger.discovery import parse_remote
from auger.models import Remote

GITHUB = Remote(host="github.com", namespace="acme", name="thing")


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/thing.git",
        "git@github.com:acme/thing",
        "https://github.com/acme/thing.git",
        "https://user:token@github.com/acme/thing.git",
        "ssh://git@github.com/acme/thing.git",
        "ssh://git@github.com:22/acme/thing.git",
        "  https://GitHub.com/acme/thing.git  ",
    ],
)
def test_it_reads_the_usual_forms(url: str) -> None:
    assert parse_remote(url) == GITHUB


def test_it_keeps_a_nested_namespace() -> None:
    remote = parse_remote("git@gitlab.com:group/team/thing.git")
    assert remote == Remote(host="gitlab.com", namespace="group/team", name="thing")
    assert remote.org_key == "gitlab.com/group/team"


def test_a_repository_at_the_top_of_a_host_has_no_namespace() -> None:
    remote = parse_remote("https://git.example/thing.git")
    assert remote == Remote(host="git.example", namespace="", name="thing")
    assert remote.org_key == "git.example"


@pytest.mark.parametrize("url", ["", "   ", "/srv/repos/thing.git", "not a url", "file:///srv/x"])
def test_it_returns_none_for_a_url_that_names_no_forge(url: str) -> None:
    assert parse_remote(url) is None

from __future__ import annotations

import pytest

from auger.net import Allowlist, Destination


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://api.github.com", Destination("api.github.com", 443)),
        ("http://api.github.com", Destination("api.github.com", 80)),
        ("http://127.0.0.1:8080/v1", Destination("127.0.0.1", 8080)),
        ("127.0.0.1:8080", Destination("127.0.0.1", 8080)),
        ("api.github.com", Destination("api.github.com", 443)),
        ("HTTPS://API.GitHub.com", Destination("api.github.com", 443)),
    ],
)
def test_it_reads_the_usual_forms(value: str, expected: Destination) -> None:
    assert Destination.parse(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "://"])
def test_an_unusable_value_parses_to_none(value: str) -> None:
    assert Destination.parse(value) is None


def test_it_allows_only_what_it_was_given() -> None:
    allowlist = Allowlist.from_values(["https://api.github.com"])
    assert allowlist.allows("api.github.com", 443)
    assert not allowlist.allows("api.github.com", 80)
    assert not allowlist.allows("evil.example", 443)


def test_a_subdomain_is_not_covered() -> None:
    """No wildcard, on purpose. An attacker controls the label to the left."""
    allowlist = Allowlist.from_values(["github.com"])
    assert not allowlist.allows("evil.github.com", 443)


def test_every_name_for_this_machine_is_covered() -> None:
    """A user writes one name for the loopback address and a client uses another."""
    allowlist = Allowlist.from_values(["http://localhost:8080"])
    assert allowlist.allows("127.0.0.1", 8080)
    assert allowlist.allows("localhost", 8080)
    assert not allowlist.allows("127.0.0.1", 9090)


def test_an_empty_allowlist_allows_nothing() -> None:
    assert not Allowlist().allows("api.github.com", 443)


def test_it_checks_a_whole_url() -> None:
    allowlist = Allowlist.from_values(["http://127.0.0.1:8080"])
    assert allowlist.allows_url("http://127.0.0.1:8080/v1/models")
    assert not allowlist.allows_url("https://api.openai.com/v1/models")

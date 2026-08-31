"""Fitting a prompt to the model that has to hold it.

A prompt over the context is not a smaller answer, it is no answer: the server rejects
the request whole and the review fails with nothing to show for the work that built it.
So the rule under test is that what comes out is always inside the budget, whatever
went in.
"""

from __future__ import annotations

import pytest

from auger.config import Config, Policy
from auger.config.schema import Backend, JobClass, ProfileEntry
from auger.jobs.prompt import CUT, fit, review_messages
from auger.llm import Gateway
from auger.llm.gateway import CHARS_PER_TOKEN, DEFAULT_PROMPT_CHARS, RESERVED_TOKENS
from auger.net import Allowlist


def total(diff: str, context: str) -> int:
    return len(diff) + len(context)


def test_what_already_fits_is_untouched() -> None:
    assert fit("a diff", "some context", 10_000) == ("a diff", "some context")


def test_no_budget_means_no_cutting() -> None:
    """A caller that does not know the model's context gets what it asked for."""
    assert fit("d" * 5000, "c" * 5000, 0) == ("d" * 5000, "c" * 5000)


@pytest.mark.parametrize("diff_len,context_len", [(100, 50_000), (50_000, 100), (40_000, 40_000)])
def test_the_result_is_always_inside_the_budget(diff_len: int, context_len: int) -> None:
    diff, context = fit("d" * diff_len, "c" * context_len, 10_000)
    assert total(diff, context) <= 10_000


def test_related_code_gives_way_before_the_diff() -> None:
    """The diff is the thing under review. Context is an aid to reading it."""
    diff, context = fit("d" * 6000, "c" * 6000, 8000)
    assert len(diff) > len(context)
    assert diff.startswith("d")


def test_a_diff_that_fits_alone_keeps_all_of_itself() -> None:
    diff, _ = fit("d" * 5000, "c" * 50_000, 10_000)
    assert diff == "d" * 5000


def test_a_diff_too_large_alone_keeps_its_head() -> None:
    """The start of a diff names the files and holds the first hunks."""
    diff, _ = fit("HEAD" + "d" * 50_000, "", 10_000)
    assert diff.startswith("HEAD")
    assert len(diff) <= 10_000


def test_a_cut_says_it_was_cut() -> None:
    """A model that thinks it saw everything reports what is missing as absent."""
    diff, context = fit("d" * 50_000, "c" * 50_000, 10_000)
    assert CUT in diff
    assert CUT in context or context == ""


def test_context_with_no_room_even_for_a_marker_is_dropped_entirely() -> None:
    """Half a sentence of related code, then a notice saying the rest was cut, teaches
    the model nothing and costs the tokens twice."""
    diff, context = fit("d" * 100, "c" * 10_000, 150)
    assert context == ""
    assert diff == "d" * 100


def test_the_prompt_carries_the_cut_through(budget: int = 4000) -> None:
    messages = review_messages(
        slug="acme/thing",
        branch="main",
        head="abc123",
        subject="a change",
        diff="d" * 40_000,
        context="c" * 40_000,
        budget=budget,
    )
    assert len(messages[1].content) < 40_000
    assert CUT in messages[1].content


# --- the budget the gateway hands out --------------------------------------------------


def gateway_for(context_tokens: int) -> Gateway:
    config = Config(
        backend={"review": Backend(url="http://127.0.0.1:9/v1", context_tokens=context_tokens)}
    )
    config.profile["balanced"].review = ProfileEntry(backend="review")
    return Gateway(config, Allowlist.from_values(["http://127.0.0.1:9"]))


def test_the_budget_follows_the_backends_context() -> None:
    """Raising the context in config gives the reviewer the room, with no other change."""
    small = gateway_for(8192).prompt_budget(JobClass.REVIEW)
    large = gateway_for(65536).prompt_budget(JobClass.REVIEW)
    assert large > small
    assert small == (8192 - RESERVED_TOKENS) * CHARS_PER_TOKEN


def test_a_context_smaller_than_the_reserve_asks_for_nothing() -> None:
    assert gateway_for(2048).prompt_budget(JobClass.REVIEW) == 0


def test_no_backend_falls_back_rather_than_raising() -> None:
    config = Config()
    config.profile["balanced"].review = ProfileEntry(backend="")
    gateway = Gateway(config, Allowlist.from_values([]))
    assert gateway.prompt_budget(JobClass.REVIEW) == DEFAULT_PROMPT_CHARS


def test_the_budget_leaves_room_for_the_answer() -> None:
    """The reply, the rules and the tool descriptions are not in the measured part."""
    tokens = 32768
    budget = gateway_for(tokens).prompt_budget(JobClass.REVIEW)
    assert budget < tokens * CHARS_PER_TOKEN


def test_the_default_backend_can_hold_a_real_review() -> None:
    """Guards the number that broke the rig: a review runs to about 17000 tokens, and a
    context of 16384 rejected every one of them."""
    assert Backend().context_tokens >= 32768


def test_a_policy_and_a_backend_agree_by_default() -> None:
    assert Policy().model_profile == "balanced"

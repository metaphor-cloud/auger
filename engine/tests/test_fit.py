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
from auger.llm.gateway import CHARS_PER_TOKEN, DEFAULT_WORKING_SET_TOKENS, RESERVED_TOKENS
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


def test_the_budget_comes_from_the_task_not_the_machine() -> None:
    """The working set is a property of the review. A machine with more memory does
    not mean a review should read more code, and deriving one from the other is what
    made every review fill a 131072 token context."""
    small = gateway_for(131_072).prompt_budget(JobClass.REVIEW, working_set_tokens=8192)
    large = gateway_for(131_072).prompt_budget(JobClass.REVIEW, working_set_tokens=16_384)
    assert small == 8192 * CHARS_PER_TOKEN
    assert large == 16_384 * CHARS_PER_TOKEN


def test_a_larger_context_does_not_raise_the_budget() -> None:
    """The ceiling clamps and never sets. This is the whole point of the separation."""
    asked = 8192
    for context in (32_768, 131_072, 262_144):
        budget = gateway_for(context).prompt_budget(JobClass.REVIEW, working_set_tokens=asked)
        assert budget == asked * CHARS_PER_TOKEN


def test_a_context_too_small_for_the_working_set_clamps_it() -> None:
    """A prompt over the context is not a smaller answer, it is no answer."""
    budget = gateway_for(16_384).prompt_budget(JobClass.REVIEW, working_set_tokens=65_536)
    assert budget == (16_384 - RESERVED_TOKENS) * CHARS_PER_TOKEN


def test_a_context_smaller_than_the_reserve_asks_for_nothing() -> None:
    assert gateway_for(2048).prompt_budget(JobClass.REVIEW, working_set_tokens=8192) == 0


def test_asking_for_nothing_uses_the_default_working_set() -> None:
    budget = gateway_for(131_072).prompt_budget(JobClass.REVIEW)
    assert budget == DEFAULT_WORKING_SET_TOKENS * CHARS_PER_TOKEN


def test_no_backend_falls_back_rather_than_raising() -> None:
    config = Config()
    config.profile["balanced"].review = ProfileEntry(backend="")
    gateway = Gateway(config, Allowlist.from_values([]))
    assert gateway.prompt_budget(JobClass.REVIEW) == DEFAULT_WORKING_SET_TOKENS * CHARS_PER_TOKEN


def test_a_backend_works_its_context_out_by_default() -> None:
    """Zero is not a small context, it is no answer yet: the supervisor reads the model
    and the machine and fills it in. A number here would be a guess about hardware
    nobody has seen."""
    assert Backend().context_tokens == 0


def test_the_server_record_is_the_ceiling_that_clamps() -> None:
    """The config holds no number when the context is worked out, so the supervisor's
    record is the only thing that knows how small the room really is."""
    gateway = gateway_for(0)
    gateway.contexts["review"] = 12_288
    budget = gateway.prompt_budget(JobClass.REVIEW, working_set_tokens=65_536)
    assert budget == (12_288 - RESERVED_TOKENS) * CHARS_PER_TOKEN


def test_an_unknown_ceiling_clamps_nothing() -> None:
    """A backend nothing has started says nothing about how much room there is, and an
    unknown ceiling is not a reason to shrink a review that was sized deliberately."""
    budget = gateway_for(0).prompt_budget(JobClass.REVIEW, working_set_tokens=16_384)
    assert budget == 16_384 * CHARS_PER_TOKEN


def test_the_default_working_set_is_small() -> None:
    """Prompt evaluation is linear in tokens, so the default is a review's size and not
    the machine's. A regression here costs minutes per review and nothing says so."""
    assert Policy().working_set_tokens == DEFAULT_WORKING_SET_TOKENS
    assert Policy().working_set_tokens <= 16_384


def test_a_policy_and_a_backend_agree_by_default() -> None:
    assert Policy().model_profile == "balanced"


# --- an answer that is all thinking ----------------------------------------------------


def test_thinking_with_no_answer_says_what_to_change() -> None:
    """A reasoning model thinks in the budget it writes in. Run out and the call
    succeeds, the server reports `stop`, and the content is empty."""
    from auger.llm.gateway import Resolved, Usage, _completion

    resolved = Resolved(
        name="local-review",
        backend=Backend(),
        entry=ProfileEntry(backend="local-review"),
        profile="balanced",
    )
    body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "", "reasoning_content": "Okay, so"},
                "finish_reason": "stop",
            }
        ]
    }
    with pytest.raises(Exception) as caught:
        _completion(body, resolved, Usage())
    assert "context_tokens" in str(caught.value)


def test_an_answer_alongside_thinking_is_kept() -> None:
    from auger.llm.gateway import Resolved, Usage, _completion

    resolved = Resolved(
        name="local-review",
        backend=Backend(),
        entry=ProfileEntry(backend="local-review"),
        profile="balanced",
    )
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": '{"findings": []}',
                    "reasoning_content": "Okay, so",
                },
                "finish_reason": "stop",
            }
        ]
    }
    assert _completion(body, resolved, Usage()).text == '{"findings": []}'


def test_the_reserve_covers_a_model_that_thinks_before_it_writes() -> None:
    """Measured: one shipped model spends about 5000 tokens reasoning before the first
    character of its answer."""
    assert RESERVED_TOKENS >= 8192


def test_a_tool_call_is_an_answer_not_a_silence() -> None:
    """A tool call has empty content by design: the model reasons, decides to run
    something, and says so instead of writing. Treating that as no answer kills the
    tool loop before it runs a single command."""
    from auger.llm.gateway import Resolved, Usage, _completion

    resolved = Resolved(
        name="local-review",
        backend=Backend(),
        entry=ProfileEntry(backend="local-review"),
        profile="balanced",
    )
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "I should look at the file first.",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "run_command", "arguments": '{"command": "ls"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    completion = _completion(body, resolved, Usage())
    assert completion.text == ""
    assert [call.name for call in completion.tool_calls] == ["run_command"]

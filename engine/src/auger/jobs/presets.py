"""Ready-made instructions for the reviewer.

The rules and the output contract are the rig's, because the parser depends on the
shape of the answer. What a review is *for* is the user's, and that is what these set:
one of them, or their own words, or both.

Each is written the way the model reads best: what to report, and what to leave alone.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    #: One line, for the window.
    summary: str
    instructions: str


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="default",
        name="As it comes",
        summary="The built-in rules, with nothing added.",
        instructions="",
    ),
    Preset(
        key="security",
        name="Security first",
        summary="Anything that leaks, escalates, or loses data. Nothing else.",
        instructions=(
            "Report only what an attacker or an accident could use: a leaked credential, "
            "an injection, a missing authorisation check, an unsafe deserialisation, a "
            "path that writes outside its directory, a secret in a log, or a change that "
            "loses or corrupts data.\n"
            "Treat a leaked credential and a missing authorisation check as critical.\n"
            "Ignore style, naming, structure, and performance."
        ),
    ),
    Preset(
        key="correctness",
        name="Correctness only",
        summary="Bugs that a test would catch, and the ones it would not.",
        instructions=(
            "Report a defect that changes what the code does: a wrong condition, an "
            "off-by-one, a race, an unhandled error, a resource that is never released, "
            "or a change that breaks an existing caller.\n"
            "Ignore style, naming, comments, and anything you would call a preference."
        ),
    ),
    Preset(
        key="performance",
        name="Performance",
        summary="Work done in the wrong place, and work done twice.",
        instructions=(
            "Report work that costs more than it needs to: a query inside a loop, a "
            "network call on a hot path, a whole collection read to answer one question, "
            "a repeated computation that could be held, or an algorithm whose cost grows "
            "faster than its input.\n"
            "Say what the cost is and when it bites. Ignore micro-optimisation."
        ),
    ),
    Preset(
        key="strict",
        name="Demanding",
        summary="Everything above, plus what makes the code hard to keep.",
        instructions=(
            "Report defects first. Then report what will make this code expensive to "
            "keep: a name that says the wrong thing, one idea implemented twice, a "
            "function that does three jobs, an abstraction with one caller, or a comment "
            "that contradicts the code.\n"
            "Judge maintainability at low severity, and never above a real defect."
        ),
    ),
    Preset(
        key="quiet",
        name="Only when it matters",
        summary="High and critical alone. Good for a repository you did not write.",
        instructions=(
            "Report only what you would stop a release for. If you are not sure it is "
            "real, do not report it.\n"
            "Use critical and high only. Return an empty list rather than filling it."
        ),
    ),
)

BY_KEY = {preset.key: preset for preset in PRESETS}


def matching(instructions: str) -> str:
    """Which preset these instructions are, or `custom` when they are the user's own."""
    text = instructions.strip()
    for preset in PRESETS:
        if preset.instructions.strip() == text:
            return preset.key
    return "custom"

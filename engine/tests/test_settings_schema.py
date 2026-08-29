"""Nothing is reachable only by editing a file."""

from __future__ import annotations

from typing import Any

import httpx

from auger.api.describe import HANDLED, describe
from auger.config.schema import (
    CodeGraph,
    Config,
    Egress,
    Models,
    Policy,
    Schedule,
)

#: A section the window edits with a form of its own, and the described list skips.
FORMS = set(HANDLED)


def paths_of(sections: list[dict[str, Any]]) -> set[str]:
    return {field["path"] for one in sections for field in one["fields"]}


def test_every_plain_setting_is_described() -> None:
    """A key added to the schema is reachable the day it is added, without anybody
    remembering to build a control for it."""
    sections, _ = describe(Config())
    described = paths_of(sections)
    for name, model in (
        ("schedule", Schedule),
        ("egress", Egress),
        ("codegraph", CodeGraph),
        ("models", Models),
        ("defaults", Policy),
    ):
        for key, field in model.model_fields.items():
            kind = field.annotation
            # Only plain values get a control. A list or a table has its own form.
            if kind in (bool, int, float, str) or str(kind).startswith(("bool", "int", "str")):
                assert f"{name}.{key}" in described, f"{name}.{key} has no control"


def test_the_settings_it_says_it_holds_are_the_ones_it_holds() -> None:
    config = Config(
        schedule=Schedule(poll_seconds=42),
        defaults=Policy(adversary=True),
    )
    sections, _ = describe(config)
    values = {field["path"]: field["value"] for one in sections for field in one["fields"]}
    assert values["schedule.poll_seconds"] == 42
    assert values["defaults.adversary"] is True


def test_a_setting_with_a_fixed_set_of_values_says_so() -> None:
    sections, _ = describe(Config())
    modes = next(
        field for one in sections for field in one["fields"] if field["path"] == "defaults.mode"
    )
    assert modes["choices"] == ["off", "draft", "complete"]


def test_a_setting_with_bounds_carries_them() -> None:
    sections, _ = describe(Config())
    poll = next(
        field
        for one in sections
        for field in one["fields"]
        if field["path"] == "schedule.poll_seconds"
    )
    assert poll["minimum"] == 5
    assert poll["kind"] == "integer"


def test_a_list_gets_no_generic_control() -> None:
    """A control for a list would be worse than the file it replaces."""
    sections, handled = describe(Config())
    assert "roots" not in {one["name"] for one in sections}
    assert "roots" in handled


async def test_the_route_serves_it(http: httpx.AsyncClient, token: str) -> None:
    async with http:
        response = await http.get("/settings/schema", headers={"Authorization": f"Bearer {token}"})
    body = response.json()
    assert response.status_code == 200
    names = {one["name"] for one in body["sections"]}
    assert {"schedule", "codegraph", "models", "defaults"} <= names
    assert "defaults" in body["handled"]


async def test_the_route_needs_a_token(http: httpx.AsyncClient) -> None:
    async with http:
        assert (await http.get("/settings/schema")).status_code == 401


#: A setting the described list cannot draw a control for, and where it is reached
#: instead. A list or a table needs a form built for its shape; the generic tab only
#: draws plain values. Anything not named here and not drawable is unreachable, which
#: is what the test below refuses.
BY_HAND = {
    "roots": "Settings, Where to look",
    "exclude": "Settings, Where to look",
    "mcp": "Settings, Tools",
    "forge": "Settings, Forges",
    "org": "Settings, Review, the overrides table",
    "repo": "Settings, Review, the overrides table",
    "backend": "Models, where a model is chosen or fetched and wired to a job class",
    "profile": "Models, and the review limits in Settings, Review",
}


def test_nothing_is_reachable_only_by_editing_the_file() -> None:
    """The rule this test exists for.

    A plain setting is drawn automatically from the schema, so it cannot go missing.
    A list or a table cannot be, so each one needs a form of its own, and this refuses
    a new one that has neither.
    """
    sections, _ = describe(Config())
    # A section of its own, or a plain value drawn at the top of the file.
    described = {one["name"] for one in sections} | paths_of(sections)
    unreachable = [
        name for name in Config.model_fields if name not in described and name not in BY_HAND
    ]
    assert unreachable == [], (
        f"these can only be changed by editing the file: {unreachable}. "
        "Build a form for it, or name where it is reached in BY_HAND."
    )


def test_a_plain_setting_cannot_go_missing() -> None:
    """Adding one to the schema is enough. The window draws it from the description."""
    from auger.config.schema import Schedule as Real

    described = paths_of(describe(Config())[0])
    for key, field in Real.model_fields.items():
        if str(field.annotation).startswith(("<class 'bool'", "<class 'int'", "<class 'str'")):
            assert f"schedule.{key}" in described

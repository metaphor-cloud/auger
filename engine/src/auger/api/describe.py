"""Every setting, described well enough for a window to draw a control for it.

A setting that only a text editor can reach is a setting most people never find. The
window builds its controls from this, so a key added to the schema is reachable the day
it is added, without anybody remembering to add a form for it.

Only the plain values are described. A list or a table has no control that would be
better than editing the file, and pretending otherwise makes a worse editor than the
file it replaces.
"""

from __future__ import annotations

from typing import Any

from auger.config.schema import Config

#: Sections the window already edits with a form built for them. They are described
#: here too, so nothing is invisible, and the window is free to skip them.
#: Sections the window builds a form of its own for. They are still described, so a
#: new key in one of them cannot go missing, but the generic list skips them rather
#: than showing every one of them a second time.
HANDLED = ("roots", "defaults", "mcp", "forge", "exclude", "schedule", "codegraph")

#: What a control can be drawn for.
KINDS = {"boolean": "boolean", "integer": "integer", "number": "number", "string": "string"}

TITLES = {
    "schedule": "Schedule",
    "egress": "Network",
    "codegraph": "Call graph",
    "models": "Model downloads",
    "defaults": "Every repository",
    "image": "Analysis image",
}


def _kind(spec: dict[str, Any]) -> str:
    """The kind of control this field takes, or `other` when none fits."""
    if "enum" in spec:
        return "string"
    kind = spec.get("type")
    if isinstance(kind, str):
        return KINDS.get(kind, "other")
    # `int | None` arrives as anyOf. Take the first type a control can be drawn for.
    for option in spec.get("anyOf", []):
        found = KINDS.get(str(option.get("type")))
        if found:
            return found
    return "other"


def _resolve(spec: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    reference = spec.get("$ref") or next(
        (one["$ref"] for one in spec.get("allOf", []) if "$ref" in one), None
    )
    if reference:
        found = defs.get(reference.rsplit("/", 1)[-1], {})
        return found if isinstance(found, dict) else {}
    return spec


def _choices(spec: dict[str, Any], defs: dict[str, Any]) -> list[str]:
    if "enum" in spec:
        return [str(one) for one in spec["enum"]]
    resolved = _resolve(spec, defs)
    return [str(one) for one in resolved.get("enum", [])]


def describe(config: Config) -> tuple[list[dict[str, Any]], list[str]]:
    """Every section of the config, with what each field is currently set to."""
    schema = Config.model_json_schema()
    defs = schema.get("$defs", {})
    values = config.model_dump(mode="json")

    sections: list[dict[str, Any]] = []
    loose: list[dict[str, Any]] = []
    for name, spec in schema.get("properties", {}).items():
        held = values.get(name)
        kind = _kind(spec)
        if kind != "other":
            # A plain value at the top of the file, such as `image`.
            loose.append(
                {
                    "key": name,
                    "path": name,
                    "kind": kind,
                    "value": held,
                    "default": spec.get("default"),
                    "choices": _choices(spec, defs),
                    "minimum": spec.get("minimum"),
                    "maximum": spec.get("maximum"),
                    "describes": spec.get("description", ""),
                }
            )
            continue
        resolved = _resolve(spec, defs)
        properties = resolved.get("properties")
        if not properties or not isinstance(held, dict):
            # A list, or a table keyed by a name the user chose. Its own form edits it.
            continue
        fields = [
            {
                "key": key,
                "path": f"{name}.{key}",
                "kind": _kind(field),
                "value": held.get(key),
                "default": field.get("default"),
                "choices": _choices(field, defs),
                "minimum": field.get("minimum"),
                "maximum": field.get("maximum"),
                "describes": field.get("description", ""),
            }
            for key, field in properties.items()
        ]
        sections.append(
            {
                "name": name,
                "title": TITLES.get(name, name.replace("_", " ").capitalize()),
                "describes": resolved.get("description", "").split("\n")[0],
                "fields": [field for field in fields if field["kind"] != "other"],
            }
        )

    if loose:
        sections.append(
            {
                "name": "file",
                "title": "Other",
                "describes": "Settings without a form of their own.",
                "fields": loose,
            }
        )
    return [one for one in sections if one["fields"]], list(HANDLED)

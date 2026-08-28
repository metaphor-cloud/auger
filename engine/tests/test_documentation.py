"""Every setting must be written down.

A configuration key that no document names is a key nobody can find. This test fails
when a key is added and the reference is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from reviewrig.config.schema import (
    Backend,
    Config,
    Egress,
    Forge,
    McpServer,
    Policy,
    ProfileEntry,
    Root,
    Schedule,
)

DOCS = Path(__file__).resolve().parents[2] / "docs"
REFERENCE = DOCS / "configuration.md"

SECTIONS: dict[str, type[BaseModel]] = {
    "root": Root,
    "policy": Policy,
    "backend": Backend,
    "profile": ProfileEntry,
    "egress": Egress,
    "schedule": Schedule,
    "forge": Forge,
    "mcp": McpServer,
}


@pytest.fixture(scope="module")
def reference() -> str:
    if not REFERENCE.exists():
        pytest.skip("the documentation is not in this checkout")
    return REFERENCE.read_text(encoding="utf-8")


@pytest.mark.parametrize(("section", "model"), list(SECTIONS.items()))
def test_every_key_of_every_section_is_documented(
    section: str, model: type[BaseModel], reference: str
) -> None:
    missing = [name for name in model.model_fields if f"`{name}`" not in reference]
    assert missing == [], f"{section} keys missing from configuration.md: {missing}"


def test_every_top_level_key_is_documented(reference: str) -> None:
    missing = [
        name
        for name in Config.model_fields
        if f"`{name}`" not in reference and f"[{name}" not in reference
    ]
    assert missing == []


def test_every_job_class_is_documented() -> None:
    models = (DOCS / "models.md").read_text(encoding="utf-8")
    for job_class in ("review", "triage", "embed", "rerank"):
        assert f"`{job_class}`" in models


@pytest.mark.parametrize(
    "name", ["install.md", "configuration.md", "models.md", "security.md", "tracker.md"]
)
def test_the_documentation_is_there(name: str) -> None:
    assert (DOCS / name).exists()


def test_the_readme_links_to_every_page() -> None:
    readme = (DOCS.parent / "README.md").read_text(encoding="utf-8")
    for name in ("install.md", "configuration.md", "models.md", "security.md", "tracker.md"):
        assert name in readme, f"README.md does not link to docs/{name}"

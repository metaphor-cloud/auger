"""Load the settings file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    """The settings, or the defaults when the file is missing."""
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}

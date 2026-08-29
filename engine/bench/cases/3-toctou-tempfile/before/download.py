"""Write a downloaded file into place."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def place(target: Path, body: bytes) -> Path:
    """Write the bytes, then move them into place in one step."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(body)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return target

"""Runtime settings for the engine process.

The Tauri host owns the port and the token. It passes both in the environment, so the
token never appears in the process arguments, where any local user could read it.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from reviewrig.config.loader import home_dir
from reviewrig.log import Level

# The webview is a different origin from the engine, so the browser sends a preflight
# before every authorised request. These are the origins a reviewrig webview can have:
# the Tauri custom scheme in a bundle, and the Vite dev server in a checkout.
DEFAULT_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "http://localhost:1420")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    token: str
    log_level: Level
    home: Path
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS

    @classmethod
    def from_env(cls) -> Settings:
        level = os.environ.get("REVIEWRIG_LOG_LEVEL", "info")
        if level not in ("debug", "info", "warn", "error"):
            level = "info"
        return cls(
            host=os.environ.get("REVIEWRIG_HOST", "127.0.0.1"),
            # Port 0 asks the operating system for a free port. The engine then prints
            # the port it got, and the host reads it from the log.
            port=int(os.environ.get("REVIEWRIG_PORT", "0")),
            # A generated token keeps a developer run usable with no host process.
            token=os.environ.get("REVIEWRIG_TOKEN") or secrets.token_urlsafe(32),
            log_level=level,  # type: ignore[arg-type]
            home=home_dir(),
            allowed_origins=cls._origins(),
        )

    @staticmethod
    def _origins() -> tuple[str, ...]:
        raw = os.environ.get("REVIEWRIG_ALLOWED_ORIGINS")
        if not raw:
            return DEFAULT_ORIGINS
        return tuple(origin.strip() for origin in raw.split(",") if origin.strip())

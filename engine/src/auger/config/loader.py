"""Read and write `config.toml`.

A user edits this file by hand and the UI edits it too, so a write must keep the
comments and the order that the user put there. `tomlkit` keeps both, as long as the
writer sets leaf values instead of replacing whole tables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import ValidationError
from tomlkit.items import Table
from tomlkit.toml_document import TOMLDocument

from auger.config.schema import Config
from auger.log import Logger, create_logger

CONFIG_NAME = "config.toml"
HOME_ENV = "AUGER_HOME"

STARTER = """\
# auger configuration.
#
# Settings merge in three levels: [defaults], then [org."..."], then [repo."..."].
# A lower level overrides the level above it.

[[roots]]
path = "~/git"
exclude = []

[defaults]
enabled = true
mode = "draft"                  # off | draft | complete
auto_review_assigned_prs = true
idle_seconds = 300              # wait this long after another agent stops
priority = 5                    # 1 highest, 9 lowest
model_profile = "balanced"
audit_hours = 24                # 0 turns whole repository audits off

# Settings for one forge organisation. A shorter key covers a whole forge.
# [org."github.com/acme"]
# mode = "complete"

# Settings for one repository. The key may be an exact path or a glob.
# [repo."~/git/acme/payments"]
# priority = 1
# hints = \"\"\"
# Treat a leaked credential as critical. Ignore style.
# \"\"\"
"""


def home_dir() -> Path:
    """Where auger keeps its config, its database, and its models."""
    return Path(os.environ.get(HOME_ENV, "~/.auger")).expanduser().absolute()


def config_path(home: Path | None = None) -> Path:
    return (home or home_dir()) / CONFIG_NAME


def ensure_config(path: Path, log: Logger | None = None) -> Path:
    """Write the starter file if none exists. Returns the path either way."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STARTER, encoding="utf-8")
        (log or create_logger("config")).info("wrote the starter config", path=str(path))
    return path


def parse(text: str) -> Config:
    """Parse config text. Raises `ValidationError` when a value is wrong."""
    return Config.model_validate(tomlkit.parse(text).unwrap())


@dataclass
class LoadResult:
    config: Config
    #: Why the file was refused, if it was. The UI shows this.
    error: str | None = None


def load_result(path: Path, log: Logger | None = None) -> LoadResult:
    """Read the config. An unreadable or invalid file falls back to the built-in defaults.

    A user edits this file by hand, so a typo must not stop the whole rig. The reason is
    logged and the UI shows it.
    """
    log = log or create_logger("config")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.info("no config file, using the defaults", path=str(path))
        return LoadResult(Config())
    except OSError as error:
        log.error("config unreadable", reason="io_error", path=str(path), error=error)
        return LoadResult(Config(), f"{path} could not be read: {error}")
    try:
        return LoadResult(parse(text))
    except (ValidationError, ValueError) as error:
        # One bad value discards the whole file, so the reason must reach the user.
        # A rig quietly running on defaults would review the wrong repositories.
        log.error("config invalid", reason="invalid_config", path=str(path), error=error)
        return LoadResult(Config(), _first_problem(error, path))


def _first_problem(error: Exception, path: Path) -> str:
    if isinstance(error, ValidationError) and error.errors():
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        return f"{path.name}: {where or 'config'} {first.get('msg', 'is invalid')}"
    return f"{path.name}: {error}"


def load(path: Path, log: Logger | None = None) -> Config:
    """The config, or the built-in defaults when the file cannot be used."""
    return load_result(path, log).config


def _serialise(config: Config) -> dict[str, Any]:
    data = config.model_dump(mode="json", exclude_none=True)
    data["org"] = {
        key: value.model_dump(mode="json", exclude_none=True) for key, value in config.org.items()
    }
    data["repo"] = {
        key: value.model_dump(mode="json", exclude_none=True) for key, value in config.repo.items()
    }
    return {key: value for key, value in data.items() if value or key == "defaults"}


def _merge(target: TOMLDocument | Table, source: dict[str, Any]) -> None:
    """Set leaf values in place, so comments and key order survive."""
    for key, value in source.items():
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            _merge(current, value)  # type: ignore[arg-type]
        elif isinstance(value, dict):
            table = tomlkit.table()
            _merge(table, value)
            target[key] = table
        else:
            target[key] = value
    for key in [key for key in target if key not in source]:
        del target[key]


def save(path: Path, config: Config) -> None:
    """Write the config back, and keep every comment that the user wrote."""
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, tomlkit.exceptions.ParseError):
        document = tomlkit.document()
    _merge(document, _serialise(config))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(document), encoding="utf-8")


def set_value(config: Config, path: str, value: object, remove: bool = False) -> Config:
    """Return a new config with one dotted path changed, or raise.

    The whole config is validated afterwards, not just the field, so a change that is
    valid on its own but wrong in context is still refused. That is what lets one route
    cover every setting without a form per key.

    A key with a dot in it, such as a repository path or a forge host, is written as
    `repo."~/git/thing".priority`.
    """
    parts = split_path(path)
    if not parts:
        raise ValueError("a change needs a setting to change")
    body = config.model_dump(mode="json", exclude_none=True)
    cursor: Any = body
    for part in parts[:-1]:
        if not isinstance(cursor, dict):
            raise ValueError(f"{path} is not a setting")
        cursor = cursor.setdefault(part, {})
    if not isinstance(cursor, dict):
        raise ValueError(f"{path} is not a setting")
    if remove:
        cursor.pop(parts[-1], None)
    else:
        cursor[parts[-1]] = value
    return Config.model_validate(body)


def split_path(path: str) -> list[str]:
    """Split on dots, except inside quotes.

    A repository key is a path and a forge key is a host, so both hold dots of their own.
    """
    parts: list[str] = []
    current = ""
    quoted = False
    for character in path.strip():
        if character == '"':
            quoted = not quoted
        elif character == "." and not quoted:
            if current:
                parts.append(current)
            current = ""
        else:
            current += character
    if current:
        parts.append(current)
    return parts

from __future__ import annotations

from pathlib import Path

import pytest

from auger.config import Config, Overrides, ensure_config, load, save
from auger.config.loader import home_dir, parse


def test_it_writes_a_starter_file(tmp_path: Path) -> None:
    path = ensure_config(tmp_path / "nested" / "config.toml")
    assert path.exists()
    assert load(path).roots[0].path == Path("~/git").expanduser().absolute()


def test_it_does_not_overwrite_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[defaults]\npriority = 1\n", encoding="utf-8")
    ensure_config(path)
    assert load(path).defaults.priority == 1


def test_a_missing_file_gives_the_built_in_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "gone.toml") == Config()


def test_an_invalid_value_falls_back_and_does_not_raise(tmp_path: Path) -> None:
    """A typo must not stop the whole rig. The reason is logged and the UI shows it."""
    path = tmp_path / "config.toml"
    path.write_text('[defaults]\nmode = "sideways"\n', encoding="utf-8")
    assert load(path) == Config()


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse('[defaults]\nmoed = "draft"\n')


def test_a_priority_outside_the_range_is_refused() -> None:
    with pytest.raises(ValueError):
        parse("[defaults]\npriority = 0\n")


def test_a_save_keeps_the_comments(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '# my rig\n[defaults]\n# the important one\npriority = 5\nmode = "draft"\n',
        encoding="utf-8",
    )
    config = load(path)
    save(
        path,
        config.model_copy(update={"defaults": config.defaults.model_copy(update={"priority": 1})}),
    )
    text = path.read_text(encoding="utf-8")
    assert "# my rig" in text
    assert "# the important one" in text
    assert "priority = 1" in text


def test_a_save_adds_a_new_section(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('# keep me\n[defaults]\nmode = "draft"\n', encoding="utf-8")
    config = load(path)
    config.org["github.com/acme"] = Overrides(mode="complete")
    save(path, config)
    reloaded = load(path)
    assert reloaded.org["github.com/acme"].mode == "complete"
    assert "# keep me" in path.read_text(encoding="utf-8")


def test_a_save_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load(ensure_config(path))
    save(path, config)
    assert load(path) == config


def test_the_home_follows_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUGER_HOME", str(tmp_path))
    assert home_dir() == tmp_path


def test_two_configs_do_not_share_their_defaults() -> None:
    """A shallow copy would let one config's change reach every config after it."""
    first = Config()
    second = Config()
    first.forge["github"].enabled = True
    first.backend["local-review"].model = "changed"
    assert second.forge["github"].enabled is False
    assert second.backend["local-review"].model == "gpt-oss"


def test_a_config_that_is_refused_says_why(tmp_path: Path) -> None:
    """One bad value discards the whole file, so the reason must reach the user."""
    from auger.config import load_result

    path = tmp_path / "config.toml"
    path.write_text("[schedule]\naudit_poll_seconds = 20\n", encoding="utf-8")
    result = load_result(path)
    assert result.config == Config()
    assert result.error is not None
    assert "audit_poll_seconds" in result.error


def test_a_config_that_is_fine_reports_no_error(tmp_path: Path) -> None:
    from auger.config import load_result

    path = tmp_path / "config.toml"
    path.write_text('[defaults]\nmode = "off"\n', encoding="utf-8")
    result = load_result(path)
    assert result.error is None
    assert result.config.defaults.mode == "off"


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    from auger.config import load_result

    assert load_result(tmp_path / "gone.toml").error is None

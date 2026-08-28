from __future__ import annotations

from pathlib import Path

from reviewrig.rig import Rig
from reviewrig.settings import Settings


def rig_with(home: Path, config: str, token: str) -> Rig:
    (home / "config.toml").write_text(config, encoding="utf-8")
    return Rig(Settings(host="127.0.0.1", port=0, token=token, log_level="debug", home=home))


def test_the_allowlist_comes_from_the_config(home: Path, token: str) -> None:
    rig = rig_with(home, '[egress]\nallow = ["http://127.0.0.1:8080"]\n', token)
    try:
        assert rig.allowlist.allows("127.0.0.1", 8080)
        assert not rig.allowlist.allows("api.openai.com", 443)
    finally:
        rig.close()


def test_a_reload_adds_a_new_destination_without_replacing_the_list(home: Path, token: str) -> None:
    """The proxy holds a reference to the list, so a reload must edit it in place."""
    rig = rig_with(home, "[egress]\nallow = []\n", token)
    try:
        assert rig.proxy.allowlist is rig.allowlist
        (home / "config.toml").write_text(
            '[egress]\nallow = ["http://127.0.0.1:9999"]\n', encoding="utf-8"
        )
        rig.reload_config()
        assert rig.proxy.allowlist.allows("127.0.0.1", 9999)
    finally:
        rig.close()

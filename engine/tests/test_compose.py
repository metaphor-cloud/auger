"""What a repository's compose file is allowed to ask for.

Each case is a document that would reach the host, and the assertion is that what comes
out the other side no longer can.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from auger.sandbox.compose import ComposeError, load, sanitise, write


def clean(text: str, root: Path) -> tuple[dict[str, Any], list[str]]:
    result = sanitise(load(text), root)
    return result.document, result.dropped


def test_a_plain_service_survives(tmp_path: Path) -> None:
    document, dropped = clean(
        """
        services:
          api:
            image: python:3.12-alpine
            command: ["python", "-m", "http.server"]
            expose: ["8000"]
        """,
        tmp_path,
    )
    assert document["services"]["api"]["image"] == "python:3.12-alpine"
    assert dropped == []


@pytest.mark.parametrize(
    "key, value",
    [
        ("privileged", True),
        ("network_mode", "host"),
        ("pid", "host"),
        ("ipc", "host"),
        ("userns_mode", "host"),
        ("cap_add", ["SYS_ADMIN"]),
        ("devices", ["/dev/kmem:/dev/kmem"]),
        ("security_opt", ["seccomp=unconfined"]),
        ("volumes_from", ["other"]),
        ("env_file", [".env"]),
        ("extra_hosts", ["host:host-gateway"]),
    ],
)
def test_a_service_may_not_ask_for_the_host(key: str, value: object, tmp_path: Path) -> None:
    text = yaml.safe_dump({"services": {"api": {"image": "alpine", key: value}}})
    document, dropped = clean(text, tmp_path)
    assert key not in document["services"]["api"]
    assert f"api: {key}" in dropped


def test_ports_are_not_published_on_the_users_machine(tmp_path: Path) -> None:
    document, dropped = clean(
        'services:\n  api:\n    image: alpine\n    ports: ["8080:80"]\n', tmp_path
    )
    assert "ports" not in document["services"]["api"]
    assert "api: ports" in dropped


def test_a_bind_mount_outside_the_project_is_dropped(tmp_path: Path) -> None:
    document, dropped = clean(
        'services:\n  api:\n    image: alpine\n    volumes: ["/etc:/etc:ro", "/:/host"]\n',
        tmp_path,
    )
    assert document["services"]["api"]["volumes"] == []
    assert len(dropped) == 2


def test_a_relative_escape_is_dropped(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    document, _ = clean(
        'services:\n  api:\n    image: alpine\n    volumes: ["../../:/host"]\n', project
    )
    assert document["services"]["api"]["volumes"] == []


def test_a_mount_inside_the_project_survives(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    document, dropped = clean(
        'services:\n  api:\n    image: alpine\n    volumes: ["./src:/app"]\n', tmp_path
    )
    assert document["services"]["api"]["volumes"] == ["./src:/app"]
    assert dropped == []


def test_a_named_volume_survives(tmp_path: Path) -> None:
    document, _ = clean(
        'services:\n  api:\n    image: alpine\n    volumes: ["cache:/cache"]\n', tmp_path
    )
    assert document["services"]["api"]["volumes"] == ["cache:/cache"]


def test_a_long_form_bind_out_of_the_project_is_dropped(tmp_path: Path) -> None:
    text = yaml.safe_dump(
        {
            "services": {
                "api": {
                    "image": "alpine",
                    "volumes": [{"type": "bind", "source": "/etc", "target": "/etc"}],
                }
            }
        }
    )
    document, _ = clean(text, tmp_path)
    assert document["services"]["api"]["volumes"] == []


def test_a_service_that_only_builds_cannot_run(tmp_path: Path) -> None:
    with pytest.raises(ComposeError):
        clean("services:\n  api:\n    build: .\n", tmp_path)


def test_a_buildable_service_is_dropped_and_the_rest_survives(tmp_path: Path) -> None:
    document, dropped = clean(
        "services:\n  api:\n    build: .\n  cache:\n    image: redis\n", tmp_path
    )
    assert set(document["services"]) == {"cache"}
    assert any("api" in line for line in dropped)


def test_interpolation_cannot_read_the_hosts_environment(tmp_path: Path) -> None:
    document, _ = clean(
        "services:\n"
        "  api:\n"
        "    image: alpine\n"
        "    environment:\n"
        "      LEAK: ${AWS_SECRET_ACCESS_KEY}\n"
        "    labels:\n"
        "      also: ${GITHUB_TOKEN}\n",
        tmp_path,
    )
    service = document["services"]["api"]
    assert service["environment"]["LEAK"] == "$${AWS_SECRET_ACCESS_KEY}"
    assert service["labels"]["also"] == "$${GITHUB_TOKEN}"


def test_top_level_secrets_and_configs_are_dropped(tmp_path: Path) -> None:
    text = yaml.safe_dump(
        {
            "services": {"api": {"image": "alpine"}},
            "secrets": {"token": {"file": "/home/someone/.aws/credentials"}},
            "configs": {"c": {"file": "/etc/passwd"}},
        }
    )
    document, dropped = clean(text, tmp_path)
    assert "secrets" not in document
    assert "configs" not in document
    assert "top level: secrets" in dropped


def test_the_loader_never_constructs_objects(tmp_path: Path) -> None:
    with pytest.raises(ComposeError):
        load("!!python/object/apply:os.system ['echo owned']")


def test_a_document_that_is_not_a_mapping_is_refused() -> None:
    with pytest.raises(ComposeError):
        load("- one\n- two\n")


def test_what_is_written_is_what_auger_produced(tmp_path: Path) -> None:
    text = "services:\n  api:\n    image: alpine\n    privileged: true\n"
    result = sanitise(load(text), tmp_path)
    path = write(result, tmp_path / "compose.yaml")
    written = yaml.safe_load(path.read_text())
    assert written == result.document
    assert "privileged" not in path.read_text()

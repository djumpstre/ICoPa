from pathlib import Path

import pytest
import yaml

from icopa_core.runtime_env.runtime_env_image_loader import (
    RuntimeEnvImageError,
    RuntimeEnvImageLoader,
)


def test_load_vm_runtime_env_template() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "configs" / "runtime_env" / "jazzy_zenoh_vm.yaml"

    payload = yaml.safe_load(env_path.read_text())
    assert payload["spec"]["images"]["runnerImage"] == ""
    with pytest.raises(RuntimeEnvImageError, match="runnerImage"):
        RuntimeEnvImageLoader.load(env_path)
    payload["spec"]["images"]["runnerImage"] = "example.invalid/icopa/zenoh_jazzy_ci:v1"
    runtime_env = RuntimeEnvImageLoader.from_dict(payload)

    assert runtime_env.name == "ros2-jazzy-zenoh"
    assert runtime_env.runner_image == "example.invalid/icopa/zenoh_jazzy_ci:v1"
    assert set(runtime_env.command_presets) == {
        "cloud_router",
        "local_router",
        "sender",
        "responder",
    }
    for preset_name in runtime_env.command_presets:
        rendered = runtime_env.render_preset_command(preset_name)
        assert "${" not in rendered
        assert runtime_env.runner_image in rendered


def test_render_preset_command_substitutes_parameters() -> None:
    runtime_env = RuntimeEnvImageLoader.from_dict(
        {
            "metadata": {"name": "example"},
            "spec": {
                "images": {"runnerImage": "docker.io/example:latest"},
                "parameters": {"target": "world"},
                "commandPresets": [
                    {
                        "name": "hello",
                        "executor": "auto",
                        "command": "docker run ${runner_image} echo hello-${target}",
                    }
                ],
            },
        }
    )

    command = runtime_env.render_preset_command("hello")

    assert command == "docker run docker.io/example:latest echo hello-world"


def test_render_preset_command_raises_on_missing_parameter() -> None:
    runtime_env = RuntimeEnvImageLoader.from_dict(
        {
            "metadata": {"name": "example"},
            "spec": {
                "images": {"runnerImage": "docker.io/example:latest"},
                "commandPresets": [
                    {"name": "bad", "executor": "auto", "command": "echo ${missing_key}"}
                ],
            },
        }
    )

    with pytest.raises(RuntimeEnvImageError, match="missing_key"):
        runtime_env.render_preset_command("bad")


def test_missing_runner_image_is_rejected() -> None:
    with pytest.raises(RuntimeEnvImageError, match="runnerImage"):
        RuntimeEnvImageLoader.from_dict(
            {
                "spec": {
                    "images": {},
                    "commandPresets": [
                        {"name": "x", "executor": "auto", "command": "echo ok"}
                    ],
                }
            }
        )


def test_load_grouped_command_runtime_env() -> None:
    runtime_env = RuntimeEnvImageLoader.from_dict(
        {
            "metadata": {"name": "grouped"},
            "spec": {
                "images": {"runnerImage": "docker.io/example:latest"},
                "parameters": {"router_ep": "tcp/127.0.0.1:7447"},
                "commandGroups": {
                    "routing": [
                        {
                            "name": "cloud_router",
                            "executor": "ssh",
                            "command": "echo cloud-${runner_image}",
                        },
                        {
                            "name": "local_router",
                            "executor": "ssh",
                            "command": "echo local-${router_ep}",
                        },
                    ],
                    "testing": [
                        {"name": "sender", "executor": "auto", "command": "echo sender"},
                    ],
                },
            },
        }
    )

    assert runtime_env.command_groups["routing"] == ["cloud_router", "local_router"]
    routing_presets = runtime_env.get_group_presets("routing")
    assert [preset.name for preset in routing_presets] == ["cloud_router", "local_router"]
    assert runtime_env.render_preset_command("local_router") == "echo local-tcp/127.0.0.1:7447"

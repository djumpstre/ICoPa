from pathlib import Path
import os
import re
import subprocess

import pytest
import yaml

from icopa_core.task_executor.base_exec import ActionExecutionError, ExecutionContext
from icopa_core.task_executor.vm_exec import VMExecBase, _resolve_static_stress_preset_container
from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import load_preset_containers
from icopa_core.task_executor.vm_runtime_actions.vm_zenoh_routing_setup import _builtin_zenoh_routing_data


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [path for path in (ROOT / "icopa_core/remote_server_cmd").rglob("*.sh") if 'IMAGE_NAME=' in path.read_text()]


def test_remote_script_discovery_is_not_empty():
    assert SCRIPTS, "Expected remote helper scripts under icopa_core/remote_server_cmd"


@pytest.mark.parametrize("path", [path for path in SCRIPTS if "HOST_CONFIG_FILE=" in path.read_text()])
def test_remote_config_defaults_are_packaged_next_to_script(path):
    content = path.read_text()
    match = re.search(r'HOST_CONFIG_FILE="\$\{HOST_CONFIG_FILE:-\$SCRIPT_DIR/([^}]+)\}"', content)
    assert match, f"Nonportable config default in {path}"
    assert (path.parent / match.group(1)).is_file()
    assert "SCRIPT_DIR=" in content


def test_public_runtime_templates_have_no_selected_image():
    checked = 0
    for path in (ROOT / "configs/runtime_env").rglob("*.yaml"):
        payload = yaml.safe_load(path.read_text())
        if not isinstance(payload, dict) or payload.get("kind") != "RuntimeEnvironment":
            continue
        assert payload["spec"]["images"]["runnerImage"] == "", path
        checked += 1
    assert checked >= 7


@pytest.mark.parametrize("image", [None, "", "  ", 123])
def test_empty_runtime_image_fails_before_rendering(image):
    executor = VMExecBase(ExecutionContext(runtime_env={"images": {"runnerImage": image}}))
    with pytest.raises(ActionExecutionError, match="your container image reference"):
        executor._render_command('docker run "${runner_image}" echo ok')


def test_user_image_and_non_container_commands_are_supported():
    executor = VMExecBase(ExecutionContext(runtime_env={"images": {"runnerImage": "example.invalid/workload:v1"}}))
    assert executor._render_command('docker run "${runner_image}" echo ok') == 'docker run "example.invalid/workload:v1" echo ok'
    assert VMExecBase(ExecutionContext())._render_command("echo native") == "echo native"


def test_public_stress_catalog_lists_but_cannot_execute_without_image(monkeypatch):
    monkeypatch.delenv("ICOPA_PRESET_CONTAINERS_PATH", raising=False)
    assert load_preset_containers()[0].image == ""
    with pytest.raises(ActionExecutionError, match="ICOPA_PRESET_CONTAINERS_PATH"):
        _resolve_static_stress_preset_container("stress_cpu_ram")


def test_explicit_router_catalog_is_not_confused_with_cached_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ICOPA_ZENOH_ROUTER_PRESETS_PATH", raising=False)
    original = _builtin_zenoh_routing_data()
    assert all(not preset["image"] for preset in original["presets_by_id"].values())
    path = tmp_path / "routers.yaml"
    payload = {"presets": [{
        "id": "custom", "image": "example.invalid/router:v1",
        "commands": [{"type": "vm_ce_router_exec_cmd", "command": "echo ${runner_image}"}],
    }]}
    path.write_text(yaml.safe_dump(payload))
    monkeypatch.setenv("ICOPA_ZENOH_ROUTER_PRESETS_PATH", str(path))
    assert _builtin_zenoh_routing_data()["presets_by_id"]["custom"]["image"] == "example.invalid/router:v1"
    monkeypatch.delenv("ICOPA_ZENOH_ROUTER_PRESETS_PATH")
    assert _builtin_zenoh_routing_data() == original


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda path: str(path.relative_to(ROOT)))
def test_remote_script_requires_an_explicit_image(path):
    subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)
    environment = dict(os.environ)
    environment.pop("IMAGE_NAME", None)
    result = subprocess.run(["bash", str(path)], env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "Set IMAGE_NAME to your container image reference" in result.stderr

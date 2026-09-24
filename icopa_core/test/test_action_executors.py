from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import pytest

from icopa_core.task_executor import (
    ActionExecutionError,
    ExecutionContext,
    GraphPingCheckExec,
    VMCleanupZenohRoutersExec,
    VMConnectCheckExec,
    VMMetricCollectionExec,
    VMProfilingBaseExec,
    VMProfilingProbeExec,
    VMProfilingRoutingSetupExec,
    VMStressContainerSetupExec,
    VMZenohProfilingProbeExec,
    VMZenohRoutingSetupExec,
    build_executor,
    resolve_executor_class,
)

_EXPECTED_STRESS_CONTAINER_VM_HOME = "icopa-cross-site-zenoh-vm-run_runtime_preset-vm_home"


@dataclass
class _FakeVMSSHResult:
    success: bool
    returncode: int
    stdout: str
    stderr: str


class _FakeVMSSHClient:
    def __init__(self, host: str, username: str, port: int = 22, key_path: str | None = None, connect_timeout: int = 8):
        self.host = host
        self.username = username
        self.port = port
        self.key_path = key_path
        self.connect_timeout = connect_timeout

    def test_connectivity(self, check_container_runtime: bool = True) -> _FakeVMSSHResult:
        return _FakeVMSSHResult(True, 0, "icopa_ssh_ok", "")

    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        return _FakeVMSSHResult(True, 0, f"ran:{command}", "")


class _FakePingSSHClient(_FakeVMSSHClient):
    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        stdout = """PING 127.0.0.2 (127.0.0.2): 56 data bytes
64 bytes from 127.0.0.2: icmp_seq=0 ttl=64 time=11.100 ms
64 bytes from 127.0.0.2: icmp_seq=1 ttl=64 time=11.500 ms

--- 127.0.0.2 ping statistics ---
2 packets transmitted, 2 packets received, 0% packet loss
round-trip min/avg/max/stddev = 11.100/11.300/11.500/0.200 ms
"""
        return _FakeVMSSHResult(True, 0, stdout, "")


class _FakeStressStartSuccessSSHClient(_FakeVMSSHClient):
    def __init__(self, host: str, username: str, port: int = 22, key_path: str | None = None, connect_timeout: int = 8):
        super().__init__(host, username, port=port, key_path=key_path, connect_timeout=connect_timeout)
        self.running_checks = 0

    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(True, 0, "container-started", "")
        if "docker ps --filter" in command:
            self.running_checks += 1
            if self.running_checks < 2:
                return _FakeVMSSHResult(True, 0, "", "")
            return _FakeVMSSHResult(
                True,
                0,
                f"{_EXPECTED_STRESS_CONTAINER_VM_HOME}|Up 2 seconds",
                "",
            )
        if "docker ps -a --filter" in command:
            return _FakeVMSSHResult(True, 0, f"{_EXPECTED_STRESS_CONTAINER_VM_HOME}|Up 2 seconds", "")
        return _FakeVMSSHResult(True, 0, "", "")


class _FakeStressNeverRunningSSHClient(_FakeVMSSHClient):
    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(True, 0, "container-started", "")
        if "docker ps --filter" in command:
            return _FakeVMSSHResult(True, 0, "", "")
        if "docker ps -a --filter" in command:
            return _FakeVMSSHResult(
                True,
                0,
                f"{_EXPECTED_STRESS_CONTAINER_VM_HOME}|Exited (1) 2 seconds ago",
                "",
            )
        if "docker logs --tail 80" in command:
            return _FakeVMSSHResult(True, 0, "stress process exited", "")
        return _FakeVMSSHResult(True, 0, "", "")


class _FakeStressAlreadyRunningSSHClient(_FakeVMSSHClient):
    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(False, 1, "", "docker run should not be called when already running")
        if "docker ps --filter" in command:
            return _FakeVMSSHResult(
                True,
                0,
                f"{_EXPECTED_STRESS_CONTAINER_VM_HOME}|Up 10 minutes",
                "",
            )
        return _FakeVMSSHResult(True, 0, "", "")


class _FakeSenderExitZeroSSHClient(_FakeVMSSHClient):
    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(True, 0, "container-started", "")
        if "docker ps --filter" in command:
            return _FakeVMSSHResult(True, 0, "", "")
        if "docker ps -a --filter" in command:
            return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-sender|Exited (0) 2 seconds ago", "")
        if "docker logs --tail 80" in command:
            return _FakeVMSSHResult(True, 0, "", "")
        return _FakeVMSSHResult(True, 0, "", "")


class _FakeSenderExitNonZeroSSHClient(_FakeVMSSHClient):
    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(True, 0, "container-started", "")
        if "docker ps --filter" in command:
            return _FakeVMSSHResult(True, 0, "", "")
        if "docker ps -a --filter" in command:
            return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-sender|Exited (1) 2 seconds ago", "")
        if "docker logs --tail 80" in command:
            return _FakeVMSSHResult(True, 0, "sender failed", "")
        return _FakeVMSSHResult(True, 0, "", "")


class _FakeSenderRunningThenExitZeroSSHClient(_FakeVMSSHClient):
    def __init__(self, host: str, username: str, port: int = 22, key_path: str | None = None, connect_timeout: int = 8):
        super().__init__(host, username, port=port, key_path=key_path, connect_timeout=connect_timeout)
        self.sender_running_checks = 0

    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(True, 0, "container-started", "")
        if "docker ps --filter" in command and "name=^/icopa-zenoh-probe-responder$" in command:
            return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-responder|Up 5 seconds", "")
        if "docker ps --filter" in command and "name=^/icopa-zenoh-probe-sender$" in command:
            self.sender_running_checks += 1
            if self.sender_running_checks <= 2:
                return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-sender|Up 5 seconds", "")
            return _FakeVMSSHResult(True, 0, "", "")
        if "docker ps -a --filter" in command and "name=^/icopa-zenoh-probe-sender$" in command:
            if self.sender_running_checks <= 2:
                return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-sender|Up 5 seconds", "")
            return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-sender|Exited (0) 1 second ago", "")
        if "docker logs --tail 80" in command:
            return _FakeVMSSHResult(True, 0, "", "")
        return _FakeVMSSHResult(True, 0, "", "")


class _FakeZenohRoutingNotRunningSSHClient(_FakeVMSSHClient):
    def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
        if "docker run -d --name" in command:
            return _FakeVMSSHResult(True, 0, "container-started", "")
        if "docker ps --filter" in command and "name=^/icopa-zenoh-router$" in command:
            return _FakeVMSSHResult(True, 0, "", "")
        if "docker ps -a --filter" in command and "name=^/icopa-zenoh-router$" in command:
            return _FakeVMSSHResult(True, 0, "icopa-zenoh-router|Exited (1) 1 second ago", "")
        if "docker logs --tail 80 icopa-zenoh-router" in command:
            return _FakeVMSSHResult(True, 0, "router crashed", "")
        return _FakeVMSSHResult(True, 0, "", "")


def _patch_vm_client(monkeypatch: pytest.MonkeyPatch, fake_cls) -> None:
    def _factory(vm_payload, *, action=None, workdir="", default_connect_timeout=8):
        action_payload = action if isinstance(action, dict) else {}
        return fake_cls(
            host=str(vm_payload.get("address") or ""),
            username=str(vm_payload.get("user_name") or vm_payload.get("username") or ""),
            port=int(vm_payload.get("port") or 22),
            key_path=str(
                vm_payload.get("key_path")
                or (vm_payload.get("credential") or {}).get("key_path")
                or ""
            ),
            connect_timeout=int(action_payload.get("connect_timeout_sec") or default_connect_timeout),
        )

    monkeypatch.setattr(
        "icopa_core.task_executor.vm_exec.build_vm_ssh_client",
        _factory,
    )


def _base_ctx(action: dict) -> ExecutionContext:
    return ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm": {
                "address": "127.0.0.2",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
        },
        runtime_env={
            "images": {"runnerImage": "docker.io/example:latest"},
            "parameters": {"target": "world", "stats_interval_sec": 5},
            "command_preset": [
                {
                    "name": "cloud_router",
                    "group": "routing",
                    "executor": "ssh",
                    "command": "echo ${runner_image}",
                },
                {
                    "name": "latency_sender",
                    "group": "profiling",
                    "executor": "ssh",
                    "command": "echo sender-${target}",
                },
            ],
        },
        action=action,
    )


def test_vm_connectivity_exec_runs_with_ssh_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    result = VMConnectCheckExec(_base_ctx({"type": "check_connectivity", "target": "vm_home"})).execute()

    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.debug["returncode"] == 0


def test_runtime_preset_exec_renders_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "routing/cloud_router",
        }
    )
    result = VMProfilingBaseExec(ctx).execute()

    assert result.success is True
    assert result.status == "SUCCESS"
    assert "docker.io/example:latest" in result.logs[0]


def test_runtime_preset_uses_preset_parameter_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "routing/cloud_router",
        }
    )
    ctx.runtime_env["command_preset"][0]["parameters"] = {"validate_metrics_dir": "/tmp/icopa/default-metrics"}
    ctx.runtime_env["command_preset"][0]["command"] = "echo ${validate_metrics_dir}"

    result = VMProfilingBaseExec(ctx).execute()
    assert result.success is True
    assert "/tmp/icopa/default-metrics" in result.logs[0]


def test_runtime_preset_injects_rrt_config_yaml_with_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "routing/cloud_router",
            "parameters": {
                "rrt_config_overrides": {
                    "icopa_sender": {
                        "ros__parameters": {"frequency_hz": 55}
                    }
                }
            },
        }
    )
    ctx.runtime_env["serializer_rrt_config_raw_yaml"] = """
icopa_sender:
  ros__parameters:
    payload_size: 256KB
"""
    ctx.runtime_env["command_preset"][0]["command"] = "echo ${rrt_config_yaml_b64}"

    result = VMProfilingBaseExec(ctx).execute()
    assert result.success is True
    rendered_cmd = str(result.debug.get("command") or "")
    encoded_text = rendered_cmd.replace("echo ", "", 1).strip()
    decoded_yaml = base64.b64decode(encoded_text.encode("ascii")).decode("utf-8")
    assert "payload_size: 256KB" in decoded_yaml
    assert "frequency_hz: 55" in decoded_yaml


def _write_stress_preset_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "Preset_Containers.yaml"
    config_path.write_text(
        """
- id: stress_cpu_ram
  name: stress_container
  aliases: [stress_container]
  image: example.invalid/icopa/icopa-stress-container:v0.1
  run_args: --network host --restart unless-stopped
  parameters:
    cpu_cores: 2
    mem_gb: 1
    duration_sec: 300
""",
        encoding="utf-8",
    )
    return config_path


def test_runtime_preset_prefers_runtime_env_over_static_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 3, "mem_gb": 2},
        }
    )
    ctx.runtime_env["command_preset"].append(
        {
            "name": "stress_cpu_ram",
            "group": "stress",
            "executor": "ssh",
            "command": "echo runtime-stress-command",
        }
    )
    result = VMProfilingBaseExec(ctx).execute()

    assert result.success is True
    assert result.debug["preset_source"] == "runtime_env"
    assert "runtime-stress-command" in result.debug["command"]


def test_runtime_preset_uses_static_stress_fallback_with_deterministic_container_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 4, "mem_gb": 2, "duration_sec": 45, "cpu_load": 75},
        }
    )
    ctx.scenario = {"metadata": {"name": "cross-site-zenoh-vm"}}
    result = VMProfilingBaseExec(ctx).execute()

    assert result.success is True
    assert result.debug["preset_source"] == "preset_containers_fallback"
    assert result.debug["resolved_static_id"] == "stress_cpu_ram"
    assert result.debug["stress_container_name"] == _EXPECTED_STRESS_CONTAINER_VM_HOME
    assert f"docker run -d --name {_EXPECTED_STRESS_CONTAINER_VM_HOME}" in result.debug["command"]
    assert "--cpu-cores 4 --mem-gb 2 --duration-sec 45 --cpu-load 75" in result.debug["command"]


def test_stress_executor_waits_until_container_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeStressStartSuccessSSHClient)
    monkeypatch.setattr("icopa_core.task_executor.vm_general_actions.vm_stress_setup.time.sleep", lambda _seconds: None)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 2, "mem_gb": 1},
            "container_check_timeout_sec": 6,
            "__vm_capabilities": {"cpu_cores": 8, "mem_total_gb": 16.0},
        }
    )
    ctx.scenario = {"metadata": {"name": "cross-site-zenoh-vm"}}
    result = VMStressContainerSetupExec(ctx).execute()

    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.debug["container_running"] is True
    assert result.debug["managed_containers_running"] == [_EXPECTED_STRESS_CONTAINER_VM_HOME]


def test_stress_executor_skips_redeploy_when_container_already_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeStressAlreadyRunningSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 2, "mem_gb": 1},
            "__vm_capabilities": {"cpu_cores": 8, "mem_total_gb": 16.0},
        }
    )
    ctx.scenario = {"metadata": {"name": "cross-site-zenoh-vm"}}
    result = VMStressContainerSetupExec(ctx).execute()

    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.debug["container_running"] is True
    assert result.debug["skipped_existing_running"] is True
    assert result.debug["managed_containers_running"] == [_EXPECTED_STRESS_CONTAINER_VM_HOME]


def test_stress_executor_fails_when_container_not_running_by_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeStressNeverRunningSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 2, "mem_gb": 1},
            "container_check_timeout_sec": 3,
            "__vm_capabilities": {"cpu_cores": 8, "mem_total_gb": 16.0},
        }
    )
    ctx.scenario = {"metadata": {"name": "cross-site-zenoh-vm"}}
    result = VMStressContainerSetupExec(ctx).execute()

    assert result.success is False
    assert result.status == "FAILED"
    assert result.debug["container_running"] is False
    assert "did not reach running state" in result.message


def test_stress_executor_rejects_requested_cpu_over_80_percent_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeStressStartSuccessSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 7, "mem_gb": 1},
            "__vm_capabilities": {"cpu_cores": 8, "mem_total_gb": 16.0},
        }
    )
    ctx.scenario = {"metadata": {"name": "cross-site-zenoh-vm"}}

    with pytest.raises(ActionExecutionError, match="cpu safety limit"):
        VMStressContainerSetupExec(ctx).execute()


def test_runtime_preset_raises_for_unknown_static_stress_preset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/not-exist",
        }
    )
    with pytest.raises(ActionExecutionError, match="not found"):
        VMProfilingBaseExec(ctx).execute()


def test_static_stress_fallback_rejects_cpu_over_capability_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cpu_cores": 5, "mem_gb": 1},
            "__vm_capabilities": {"cpu_cores": 4, "mem_total_gb": 8.0},
        }
    )
    with pytest.raises(ActionExecutionError, match="cpu safety limit"):
        VMProfilingBaseExec(ctx).execute()


def test_static_stress_fallback_supports_cleanup_only_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    config_path = _write_stress_preset_config(tmp_path)
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))
    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")

    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "stress/stress_cpu_ram",
            "parameters": {"cleanup_only": True},
        }
    )
    ctx.scenario = {"metadata": {"name": "cross-site-zenoh-vm"}}
    result = VMProfilingBaseExec(ctx).execute()
    assert result.success is True
    assert result.debug["preset_source"] == "preset_containers_fallback"
    assert f"docker rm -f {_EXPECTED_STRESS_CONTAINER_VM_HOME}" in result.debug["command"]
    assert "docker run -d" not in result.debug["command"]


def test_runtime_preset_raises_for_missing_template_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = _base_ctx(
        {
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "routing/cloud_router",
        }
    )
    ctx.runtime_env["command_preset"][0]["command"] = "echo ${missing_key}"

    with pytest.raises(ActionExecutionError, match="missing_key"):
        VMProfilingBaseExec(ctx).execute()


def test_metric_collection_runs_for_all_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    result = VMMetricCollectionExec(
        _base_ctx({"type": "collect_metrics", "target": "vm_home"})
    ).execute()

    assert result.success is True
    assert result.status == "SUCCESS"
    assert len(result.logs) == 1


def test_action_registry_uses_specialized_runtime_executors() -> None:
    assert resolve_executor_class({"type": "run_runtime_preset", "preset": "stress/stress_cpu_ram"}) is VMStressContainerSetupExec
    assert resolve_executor_class({"type": "run_runtime_preset", "preset": "routing/cloud_router"}) is VMProfilingRoutingSetupExec
    assert resolve_executor_class({"type": "run_runtime_preset", "preset": "profiling/latency_sender"}) is VMProfilingProbeExec
    assert resolve_executor_class({"type": "check_connectivity"}) is VMConnectCheckExec
    assert resolve_executor_class({"type": "check_graph_ping"}) is GraphPingCheckExec
    assert resolve_executor_class({"type": "cleanup_zenoh_routers"}) is VMCleanupZenohRoutersExec


def test_build_executor_constructs_executor_instance() -> None:
    executor = build_executor(_base_ctx({"type": "collect_metrics", "target": "vm_home"}))
    assert isinstance(executor, VMMetricCollectionExec)


def test_build_executor_uses_stress_executor_for_stress_presets() -> None:
    ctx = _base_ctx({"type": "run_runtime_preset", "target": "vm_home", "preset": "stress/stress_cpu_ram"})
    executor = build_executor(ctx)
    assert isinstance(executor, VMStressContainerSetupExec)


def test_build_executor_uses_zenoh_routing_executor_for_routing_presets() -> None:
    ctx = _base_ctx({"type": "run_runtime_preset", "target": "vm_home", "preset": "routing/local_router"})
    ctx.runtime_env["tags"] = {"zenoh": {"enabled": True, "routerPort": 7447}}
    executor = build_executor(ctx)
    assert isinstance(executor, VMZenohRoutingSetupExec)


def test_build_executor_uses_zenoh_profiling_executor_for_profiling_presets() -> None:
    ctx = _base_ctx({"type": "run_runtime_preset", "target": "vm_home", "preset": "profiling/latency_sender"})
    ctx.runtime_env["tags"] = {"zenoh": {"enabled": True, "routerPort": 7447}}
    executor = build_executor(ctx)
    assert isinstance(executor, VMZenohProfilingProbeExec)


def test_graph_ping_check_collects_latency_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakePingSSHClient)
    result = GraphPingCheckExec(
        _base_ctx({"type": "check_graph_ping", "source_node": "vm_home", "target_node": "cloud_vm"})
    ).execute()

    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.metrics["duration_sec"] == 60
    assert result.metrics["latency_avg_ms"] == pytest.approx(11.3)
    assert result.metrics["latency_variance_ms"] == pytest.approx(0.2)


def test_zenoh_routing_clean_router_works_without_runtime_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    result = VMZenohRoutingSetupExec(
        _base_ctx({"type": "run_runtime_preset", "target": "vm_home", "preset": "routing/clean_router"})
    ).execute()
    assert result.success is True
    assert result.status == "SUCCESS"
    assert "docker rm -f" in result.debug["command_final"]


def test_zenoh_routing_uses_cloud_endpoint_from_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 9099, "purpose": "zenoh-router"},
                    ]
                },
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "name": "local_router",
                    "group": "routing",
                    "executor": "ssh",
                    "command": "echo ${cloud_router_ep}",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "routing/local_router",
        },
    )
    result = VMZenohRoutingSetupExec(ctx).execute()
    assert result.success is True
    assert "tcp/198.51.100.10:9099" in result.debug["command_final"]
    assert result.debug["computed_cloud_router_ep"] == "tcp/198.51.100.10:9099"


def test_zenoh_routing_prefers_explicit_peer_node(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {"openPorts": [{"proto": "tcp", "port": 7447, "purpose": "zenoh-router"}]},
            },
            "vm_lab_edge": {
                "address": "203.0.113.55",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {"openPorts": [{"proto": "tcp", "port": 18080, "purpose": "zenoh-router"}]},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "name": "local_router",
                    "group": "routing",
                    "executor": "ssh",
                    "command": "echo ${cloud_router_ep}",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "routing/local_router",
            "parameters": {"peer_node": "vm_lab_edge"},
        },
    )
    result = VMZenohRoutingSetupExec(ctx).execute()
    assert result.success is True
    assert "tcp/203.0.113.55:18080" in result.debug["command_final"]
    assert result.debug["computed_cloud_router_ep"] == "tcp/203.0.113.55:18080"


def test_zenoh_routing_uses_preset_container_id_and_exec_cmd_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 7447, "purpose": "zenoh-router"},
                    ]
                },
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [],
        },
        action={
            "type": "run_runtime_preset",
            "target": "cloud_vm_bw",
            "preset": "routing/setup_zenoh_routing/jazzy_v1",
            "parameters": {
                "preset_container_id": "zenoh_router:jazzy_v1",
                "exec_cmd_type": "vm_ce_router_exec_cmd",
            },
        },
    )
    result = VMZenohRoutingSetupExec(ctx).execute()
    assert result.success is True
    assert result.debug["preset"]["preset_container_id"] == "zenoh_router:jazzy_v1"
    assert result.debug["preset"]["exec_cmd_type"] == "vm_ce_router_exec_cmd"
    assert "docker run -d" in result.debug["command_final"]
    assert "docker rm -f" not in result.debug["command_final"]


def test_zenoh_routing_cloud_exec_uses_target_node_inventory_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 9099, "purpose": "zenoh-router"},
                    ]
                },
            },
            "vm_home_edge": {
                "address": "192.0.2.55",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 18080, "purpose": "zenoh-router"},
                    ]
                },
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "parameters": {},
            "command_preset": [],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home_edge",
            "preset": "routing/setup_zenoh_routing/jazzy_v1",
            "parameters": {
                "preset_container_id": "zenoh_router:jazzy_v1",
                "exec_cmd_type": "vm_ce_router_exec_cmd",
            },
        },
    )

    result = VMZenohRoutingSetupExec(ctx).execute()
    assert result.success is True
    assert result.debug["cloud_router_node"] == "vm_home_edge"
    assert result.debug["required_router_port"] == 18080
    assert result.debug["computed_cloud_router_ep"] == "tcp/192.0.2.55:18080"
    assert "18080" in result.debug["command_final"]
    assert '"endpoints": ["tcp/0.0.0.0:7447", "tcp/0.0.0.0:18080"]' in result.debug["command_final"]
    assert '"tcp/0.0.0.0:7447"' in result.debug["command_final"]
    assert '"tcp/0.0.0.0:9099"' not in result.debug["command_final"]
    assert "'tcp/0.0.0.0:7447'" not in result.debug["command_final"]
    assert "'tcp/0.0.0.0:18080'" not in result.debug["command_final"]


def test_zenoh_routing_prefers_runtime_runner_image_over_builtin_preset_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 7447, "purpose": "zenoh-router"},
                    ]
                },
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "parameters": {},
            "command_preset": [],
        },
        action={
            "type": "run_runtime_preset",
            "target": "cloud_vm_bw",
            "preset": "routing/setup_zenoh_routing/jazzy_v1",
            "parameters": {
                "preset_container_id": "zenoh_router:jazzy_v1",
                "exec_cmd_type": "vm_ce_router_exec_cmd",
            },
        },
    )

    result = VMZenohRoutingSetupExec(ctx).execute()
    assert result.success is True
    assert "docker.io/example:latest" in result.debug["command_final"]
    assert "example.invalid/icopa/zenoh_jazzy_ci:v2" not in result.debug["command_final"]


def test_zenoh_routing_fails_when_router_container_is_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_vm_client(monkeypatch, _FakeZenohRoutingNotRunningSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 7447, "purpose": "zenoh-router"},
                    ]
                },
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "parameters": {},
            "command_preset": [],
        },
        action={
            "type": "run_runtime_preset",
            "target": "cloud_vm_bw",
            "preset": "routing/setup_zenoh_routing/jazzy_v1",
            "parameters": {
                "preset_container_id": "zenoh_router:jazzy_v1",
                "exec_cmd_type": "vm_ce_router_exec_cmd",
            },
        },
    )

    result = VMZenohRoutingSetupExec(ctx).execute()
    assert result.success is False
    assert result.status == "FAILED"
    assert result.debug["container_running"] is False
    assert "not running" in result.message


def test_cleanup_zenoh_routers_executor_runs_cleanup_command(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCleanupSSHClient(_FakeVMSSHClient):
        class _Status:
            exists = False
            status = ""

        def get_container_status(self, container_name: str, timeout: int = 20):
            return self._Status()

    _patch_vm_client(monkeypatch, _FakeCleanupSSHClient)
    result = VMCleanupZenohRoutersExec(
        _base_ctx({"type": "cleanup_zenoh_routers", "target": "vm_home"})
    ).execute()
    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.debug["container_names"] == ["icopa-zenoh-router", "zenoh-router"]
    assert len(result.debug["containers"]) == 2


def test_zenoh_profiling_maps_latency_sender_to_testing_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {
                    "openPorts": [
                        {"proto": "tcp", "port": 9099, "purpose": "zenoh-router"},
                    ]
                },
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "sender",
                    "executor": "ssh",
                    "command": "docker run --rm --network host ${runner_image} /bin/bash -lc 'echo sender'",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "profiling/latency_sender",
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is True
    assert result.debug["resolved_preset"]["group"] == "testing"
    assert "--name icopa-zenoh-probe-sender" in result.debug["command_rendered"]
    assert "docker run -d" in result.debug["command_rendered"]


def test_zenoh_profiling_prefers_explicit_peer_node(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeVMSSHClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_sender": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {"openPorts": [{"proto": "tcp", "port": 7447, "purpose": "zenoh-router"}]},
            },
            "vm_lab_edge": {
                "address": "203.0.113.55",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
                "networking": {"openPorts": [{"proto": "tcp", "port": 20001, "purpose": "zenoh-router"}]},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "responder",
                    "executor": "ssh",
                    "command": "echo ${cloud_router_ep}",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_sender",
            "preset": "profiling/latency_responder",
            "parameters": {"peer_node": "vm_lab_edge"},
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is True
    assert "tcp/203.0.113.55:20001" in result.debug["command_final"]
    assert result.debug["computed_cloud_router_ep"] == "tcp/203.0.113.55:20001"


def test_zenoh_profiling_fails_when_command_execution_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFailedCommandClient(_FakeVMSSHClient):
        def execute_command(self, command: str, timeout: int = 20) -> _FakeVMSSHResult:
            if "docker ps --filter" in command:
                return _FakeVMSSHResult(True, 0, "", "")
            if "docker ps -a --filter" in command:
                return _FakeVMSSHResult(True, 0, "icopa-zenoh-probe-sender|Exited (1) 2 seconds ago", "")
            if "docker logs --tail 80" in command:
                return _FakeVMSSHResult(True, 0, "launch failed", "")
            return _FakeVMSSHResult(False, 1, "", "docker run failed")

    _patch_vm_client(monkeypatch, _FakeFailedCommandClient)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "sender",
                    "executor": "ssh",
                    "command": "docker run --rm --network host ${runner_image} /bin/bash -lc 'echo sender'",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "profiling/latency_sender",
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is False
    assert result.status == "FAILED"
    assert result.debug["returncode"] == 1
    assert "Exited" in result.debug["container_status_all"]


def test_zenoh_profiling_sender_marks_success_after_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeSenderExitZeroSSHClient)
    monkeypatch.setattr("icopa_core.task_executor.vm_runtime_actions.vm_zenoh_profiling.time.sleep", lambda _seconds: None)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "sender",
                    "executor": "ssh",
                    "command": "docker run --rm --network host ${runner_image} /bin/bash -lc 'echo sender'",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "profiling/latency_sender",
            "completion_timeout_sec": 10,
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.debug["sender_completion_expected"] is True
    assert result.debug["sender_completed"] is True
    assert result.debug["sender_completion_state"] == "exited_0"


def test_zenoh_profiling_rewrites_legacy_netanalyzer_validate_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeSenderExitZeroSSHClient)
    monkeypatch.setattr("icopa_core.task_executor.vm_runtime_actions.vm_zenoh_profiling.time.sleep", lambda _seconds: None)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "sender",
                    "executor": "ssh",
                    "command": (
                        'host_validate_dir="/tmp/netanalyzer/validate-$(date -u +%Y%m%d-%H%M%S)"\n'
                        'mkdir -p "$host_validate_dir"\n'
                        'docker run --rm --network host -v "$host_validate_dir:/tmp" ${runner_image} '
                        "/bin/bash -lc 'echo sender'"
                    ),
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "profiling/latency_sender",
            "parameters": {
                "validate_metrics_dir": "/tmp/icopa/generated/test/run_1/metrics/run-001",
            },
            "completion_timeout_sec": 10,
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is True
    assert result.debug["legacy_validate_dir_rewritten"] is True
    assert "/tmp/netanalyzer" not in result.debug["command_final"]
    assert "/tmp/icopa/generated/test/run_1/metrics/run-001" in result.debug["command_final"]


def test_zenoh_profiling_sender_checks_running_then_waits_for_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeSenderRunningThenExitZeroSSHClient)
    monkeypatch.setattr("icopa_core.task_executor.vm_runtime_actions.vm_zenoh_profiling.time.sleep", lambda _seconds: None)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "sender",
                    "executor": "ssh",
                    "command": "docker run --rm --network host ${runner_image} /bin/bash -lc 'echo sender'",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "profiling/latency_sender",
            "probe_startup_wait_sec": 10,
            "completion_poll_interval_sec": 2,
            "completion_timeout_sec": 30,
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.debug["probe_startup_wait_sec"] == 10
    assert result.debug["probe_completion_poll_interval_sec"] == 2
    assert result.debug["probe_completion_timeout_sec"] == 30
    assert result.debug["probe_startup_both_containers_running"] is True
    assert result.debug["sender_completion_state"] == "exited_0"


def test_zenoh_profiling_sender_marks_failed_on_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_vm_client(monkeypatch, _FakeSenderExitNonZeroSSHClient)
    monkeypatch.setattr("icopa_core.task_executor.vm_runtime_actions.vm_zenoh_profiling.time.sleep", lambda _seconds: None)
    ctx = ExecutionContext(
        inventory_by_node={
            "vm_home": {
                "address": "127.0.0.1",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
            "cloud_vm_bw": {
                "address": "198.51.100.10",
                "user_name": "ubuntu",
                "port": 22,
                "credential": {"key_path": __file__},
            },
        },
        runtime_env={
            "tags": {"zenoh": {"enabled": True, "routerPort": 7447}},
            "images": {"runnerImage": "docker.io/example:latest"},
            "command_preset": [
                {
                    "group": "testing",
                    "name": "sender",
                    "executor": "ssh",
                    "command": "docker run --rm --network host ${runner_image} /bin/bash -lc 'echo sender'",
                }
            ],
        },
        action={
            "type": "run_runtime_preset",
            "target": "vm_home",
            "preset": "profiling/latency_sender",
            "completion_timeout_sec": 10,
        },
    )
    result = VMZenohProfilingProbeExec(ctx).execute()
    assert result.success is False
    assert result.status == "FAILED"
    assert result.debug["sender_completion_expected"] is True
    assert result.debug["sender_completed"] is False
    assert result.debug["sender_completion_state"] == "exited_non_zero"
    assert "sender failed" in result.debug["container_logs_tail"]

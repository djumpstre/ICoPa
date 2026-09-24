"""Zenoh profiling executors for VM-based responder/sender deployment."""

from __future__ import annotations

import re
import time
from typing import Any

from ..base_exec import ActionExecutionError, ActionExecutionResult
from ..vm_exec import VMProfilingProbeExec
from .vm_zenoh_routing_setup import _extract_zenoh_port_from_vm

_DEFAULT_CLOUD_NODE_NAME = "cloud_vm_bw"
_DOCKER_NAME_PATTERN = re.compile(r"--name\s+\S+")

_CONTAINER_NAMES_BY_PRESET: dict[str, tuple[str, list[str]]] = {
    "profiling/latency_responder": (
        "icopa-zenoh-probe-responder",
        ["icopa-zenoh-latency-responder", "zenoh-latency-responder", "icopa-zenoh-responder"],
    ),
    "profiling/latency_sender": (
        "icopa-zenoh-probe-sender",
        ["icopa-zenoh-latency-sender", "zenoh-latency-sender", "icopa-zenoh-sender"],
    ),
}

_PRESET_FALLBACK_ALIASES: dict[str, list[tuple[str, str]]] = {
    "profiling/latency_responder": [("testing", "responder"), ("profiling", "latency_responder"), ("", "responder")],
    "profiling/latency_sender": [("testing", "sender"), ("profiling", "latency_sender"), ("", "sender")],
}

_SENDER_PRESET_NAME = "profiling/latency_sender"
_PROBE_STARTUP_WAIT_SEC = 10
_PROBE_COMPLETION_POLL_INTERVAL_SEC = 2
_DEFAULT_PROBE_COMPLETION_TIMEOUT_SEC = 180
_PROBE_CLEANUP_POLL_INTERVAL_SEC = 2
_DEFAULT_PROBE_CLEANUP_TIMEOUT_SEC = 30
_LEGACY_VALIDATE_DIR_ASSIGN_PATTERN = re.compile(r'host_validate_dir\s*=\s*"[^"]*/tmp/netanalyzer[^"]*"')
_PROBE_PREPARE_METRICS_BLOCK = """\
host_validate_dir="${icopa_metrics_dir}"
mkdir -p "$host_validate_dir"
host_rrt_config_file="$host_validate_dir/rrt_config.yaml"
if [[ -n "${rrt_config_yaml_b64}" ]]; then
  printf '%s' "${rrt_config_yaml_b64}" | base64 -d > "$host_rrt_config_file"
else
  : > "$host_rrt_config_file"
fi
echo "ICOPA_VALIDATE_DIR=$host_validate_dir"
"""


def _normalize_profiling_preset(raw_preset: str) -> str:
    text = str(raw_preset or "").strip()
    if not text:
        return text
    if text in {"profiling/responder", "responder"}:
        return "profiling/latency_responder"
    if text in {"profiling/sender", "sender"}:
        return "profiling/latency_sender"
    if "/" not in text:
        return f"profiling/{text}"
    return text


class VMZenohProfilingProbeExec(VMProfilingProbeExec):
    """Run zenoh profiling presets as detached named containers."""

    action_type = "run_runtime_preset:profiling:zenoh"

    def _resolve_container_names(self, preset_name: str) -> tuple[str, list[str]]:
        if preset_name in _CONTAINER_NAMES_BY_PRESET:
            name, legacy = _CONTAINER_NAMES_BY_PRESET[preset_name]
            return name, list(legacy)
        action_params = self.ctx.action.get("parameters")
        if isinstance(action_params, dict):
            custom_name = action_params.get("container_name")
            if custom_name:
                return str(custom_name), []
        return "icopa-zenoh-profiling", []

    def _resolve_cloud_vm(self, target_node: str) -> dict[str, Any]:
        action_params = self.ctx.action.get("parameters")
        explicit_peer = ""
        if isinstance(action_params, dict):
            explicit_peer = str(action_params.get("peer_node") or action_params.get("peerNode") or "").strip()
        if not explicit_peer:
            explicit_peer = str(self.ctx.action.get("peer_node") or self.ctx.action.get("peerNode") or "").strip()
        if explicit_peer:
            if explicit_peer not in self.ctx.inventory_by_node:
                raise ActionExecutionError(
                    f"peer_node '{explicit_peer}' was not found in inventory_by_node."
                )
            return self.ctx.inventory_by_node[explicit_peer]
        if _DEFAULT_CLOUD_NODE_NAME in self.ctx.inventory_by_node:
            return self.ctx.inventory_by_node[_DEFAULT_CLOUD_NODE_NAME]
        if target_node != _DEFAULT_CLOUD_NODE_NAME and target_node in self.ctx.inventory_by_node:
            return self.ctx.inventory_by_node[target_node]
        for node_name, vm in self.ctx.inventory_by_node.items():
            if node_name != target_node:
                return vm
        return self.ctx.inventory_by_node.get(target_node, {})

    def _resolve_cloud_router_endpoint(self, target_node: str) -> str:
        cloud_vm = self._resolve_cloud_vm(target_node)
        host = str(cloud_vm.get("address") or "")
        if not host:
            return ""
        port = _extract_zenoh_port_from_vm(cloud_vm)
        if port is None:
            tags = self.ctx.runtime_env.get("tags")
            if isinstance(tags, dict):
                zenoh = tags.get("zenoh")
                if isinstance(zenoh, dict):
                    raw_port = zenoh.get("routerPort") or zenoh.get("router_port")
                    if isinstance(raw_port, int):
                        port = raw_port
                    elif isinstance(raw_port, str) and raw_port.isdigit():
                        port = int(raw_port)
        if port is None:
            port = 7447
        return f"tcp/{host}:{port}"

    def _build_template_values(self) -> dict[str, Any]:
        values = super()._build_template_values()
        target_node = str(self.ctx.action.get("target") or "")
        endpoint = self._resolve_cloud_router_endpoint(target_node)
        if endpoint:
            values["cloud_router_ep"] = endpoint
            values["cloudRouterEp"] = endpoint
        return values

    def _resolve_runtime_preset(self) -> dict[str, Any]:
        requested = _normalize_profiling_preset(str(self.ctx.require_action_field("preset")))
        self.ctx.action["preset"] = requested
        try:
            return super()._resolve_runtime_preset()
        except ActionExecutionError as original_exc:
            aliases = _PRESET_FALLBACK_ALIASES.get(requested, [])
            for alias_group, alias_name in aliases:
                for preset in self.ctx.get_runtime_presets():
                    name = str(preset.get("name") or "")
                    group = str(preset.get("group") or "")
                    if name != alias_name:
                        continue
                    if alias_group and group != alias_group:
                        continue
                    return preset
            raise original_exc

    def _render_probe_command(
        self,
        *,
        preset_name: str,
        command_template: str,
    ) -> tuple[str, str, str, bool]:
        container_name, legacy_names = self._resolve_container_names(preset_name)
        cleanup_targets = [container_name, *legacy_names, "zenoh-probe-responder", "zenoh-probe-sender"]
        cleanup_command = f"docker rm -f {' '.join(cleanup_targets)} >/dev/null 2>&1 || true"

        normalized_command_template = command_template
        normalized_legacy_validate_dir = False
        if "${validate_metrics_dir}" not in command_template:
            rewritten, count = _LEGACY_VALIDATE_DIR_ASSIGN_PATTERN.subn(
                'host_validate_dir="${validate_metrics_dir}"',
                command_template,
                count=1,
            )
            if count > 0:
                normalized_command_template = rewritten
                normalized_legacy_validate_dir = True

        rendered_command = self._render_command(normalized_command_template)
        if "docker run" in rendered_command:
            rendered_command = rendered_command.replace("docker run --rm", "docker run -d", 1)
            if "docker run -d" not in rendered_command:
                rendered_command = rendered_command.replace("docker run", "docker run -d", 1)
            if "--name" in rendered_command:
                rendered_command = _DOCKER_NAME_PATTERN.sub(f"--name {container_name}", rendered_command, count=1)
            else:
                rendered_command = rendered_command.replace(
                    "docker run -d",
                    f"docker run -d --name {container_name}",
                    1,
                )

        setup_command = self._render_command(_PROBE_PREPARE_METRICS_BLOCK)
        final_command = f"{cleanup_command}\n{setup_command}\n{rendered_command}".strip()
        return container_name, rendered_command, final_command, normalized_legacy_validate_dir

    @staticmethod
    def _is_container_running(container_name: str, check_stdout: str) -> bool:
        text = str(check_stdout or "")
        if container_name not in text:
            return False
        status_text = text.split("|", 1)[1].strip().lower() if "|" in text else text.lower()
        return status_text.startswith("up")

    @staticmethod
    def _status_line_for_container(container_name: str, status_text: str) -> str:
        for line in str(status_text or "").splitlines():
            line_text = line.strip()
            if not line_text:
                continue
            if container_name in line_text:
                return line_text
        return ""

    def _is_sender_preset(self, requested_preset: str) -> bool:
        return requested_preset == _SENDER_PRESET_NAME

    def _completion_timeout_sec(self) -> int:
        raw = self.ctx.action.get("completion_timeout_sec", _DEFAULT_PROBE_COMPLETION_TIMEOUT_SEC)
        try:
            timeout_sec = int(raw)
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("completion_timeout_sec must be an integer.") from exc
        if timeout_sec <= 0:
            raise ActionExecutionError("completion_timeout_sec must be > 0.")
        return timeout_sec

    def _completion_poll_interval_sec(self) -> int:
        raw = self.ctx.action.get("completion_poll_interval_sec", _PROBE_COMPLETION_POLL_INTERVAL_SEC)
        try:
            poll_interval = int(raw)
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("completion_poll_interval_sec must be an integer.") from exc
        if poll_interval <= 0:
            raise ActionExecutionError("completion_poll_interval_sec must be > 0.")
        return poll_interval

    def _probe_startup_wait_sec(self) -> int:
        raw = self.ctx.action.get("probe_startup_wait_sec", _PROBE_STARTUP_WAIT_SEC)
        try:
            wait_sec = int(raw)
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("probe_startup_wait_sec must be an integer.") from exc
        if wait_sec < 0:
            raise ActionExecutionError("probe_startup_wait_sec must be >= 0.")
        return wait_sec

    def _is_cleanup_phase(self) -> bool:
        phase = self.ctx.phase if isinstance(self.ctx.phase, dict) else {}
        phase_name = str(phase.get("name") or "").strip().lower()
        return phase_name.startswith("cleanup")

    def _cleanup_only_requested(self) -> bool:
        if self._is_cleanup_phase():
            return True
        params = self.ctx.action.get("parameters")
        if isinstance(params, dict):
            raw = params.get("cleanup_only")
            if raw is not None:
                return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
        raw_top = self.ctx.action.get("cleanup_only")
        if raw_top is None:
            return False
        return str(raw_top).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _cleanup_timeout_sec(self) -> int:
        params = self.ctx.action.get("parameters")
        raw = None
        if isinstance(params, dict):
            raw = params.get("cleanup_timeout_sec")
        if raw is None:
            raw = self.ctx.action.get("cleanup_timeout_sec", _DEFAULT_PROBE_CLEANUP_TIMEOUT_SEC)
        try:
            timeout_sec = int(raw)
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("cleanup_timeout_sec must be an integer.") from exc
        if timeout_sec <= 0:
            raise ActionExecutionError("cleanup_timeout_sec must be > 0.")
        return timeout_sec

    def _cleanup_poll_interval_sec(self) -> int:
        params = self.ctx.action.get("parameters")
        raw = None
        if isinstance(params, dict):
            raw = params.get("cleanup_poll_interval_sec")
        if raw is None:
            raw = self.ctx.action.get("cleanup_poll_interval_sec", _PROBE_CLEANUP_POLL_INTERVAL_SEC)
        try:
            poll_interval = int(raw)
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("cleanup_poll_interval_sec must be an integer.") from exc
        if poll_interval <= 0:
            raise ActionExecutionError("cleanup_poll_interval_sec must be > 0.")
        return poll_interval

    def _execute_cleanup_only(
        self,
        *,
        vm: dict[str, Any],
        target: str,
        requested_preset: str,
    ) -> ActionExecutionResult:
        container_name, legacy_names = self._resolve_container_names(requested_preset)
        cleanup_targets: list[str] = []
        for item in [container_name, *legacy_names, "zenoh-probe-responder", "zenoh-probe-sender"]:
            text = str(item or "").strip()
            if text and text not in cleanup_targets:
                cleanup_targets.append(text)
        cleanup_command = f"docker rm -f {' '.join(cleanup_targets)} >/dev/null 2>&1 || true"

        timeout_sec = int(self.ctx.action.get("timeout_sec") or 45)
        cleanup_timeout_sec = self._cleanup_timeout_sec()
        cleanup_poll_interval_sec = self._cleanup_poll_interval_sec()
        ssh_client = self._build_ssh_client(vm)
        remove_result = ssh_client.execute_command(cleanup_command, timeout=timeout_sec)

        deadline = time.time() + cleanup_timeout_sec
        polls = 0
        remaining: list[str] = []
        while True:
            polls += 1
            remaining = []
            for name in cleanup_targets:
                status = ssh_client.get_container_status(name, timeout=15)
                if status.exists:
                    remaining.append(name)
            if not remaining:
                break
            if time.time() >= deadline:
                break
            time.sleep(cleanup_poll_interval_sec)

        success = bool(remove_result.success and not remaining)
        return ActionExecutionResult(
            action_type=self.action_type,
            target=target,
            success=success,
            status="SUCCESS" if success else "FAILED",
            message=(
                f"Zenoh profiling cleanup completed for '{requested_preset}' on '{target}'."
                if success
                else f"Zenoh profiling cleanup failed for '{requested_preset}' on '{target}'."
            ),
            logs=[remove_result.stdout] if remove_result.stdout else [],
            debug={
                "requested_preset": requested_preset,
                "cleanup_only": True,
                "cleanup_targets": cleanup_targets,
                "cleanup_command": cleanup_command,
                "cleanup_timeout_sec": cleanup_timeout_sec,
                "cleanup_poll_interval_sec": cleanup_poll_interval_sec,
                "cleanup_polls": polls,
                "cleanup_remaining_containers": remaining,
                "returncode": remove_result.returncode,
                "stderr": remove_result.stderr,
                "vm": {
                    "host": vm.get("address", ""),
                    "user": vm.get("user_name", ""),
                    "port": vm.get("port", 22),
                },
            },
        )

    def _container_running_snapshot(self, *, ssh_client, container_name: str) -> tuple[bool, str]:
        running_check_cmd = (
            f"docker ps --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )
        running_result = ssh_client.execute_command(running_check_cmd, timeout=15)
        running_status = running_result.stdout or running_result.stderr or ""
        is_running = running_result.success and self._is_container_running(container_name, running_result.stdout)
        return is_running, running_status

    def _wait_sender_completion(
        self,
        *,
        ssh_client,
        container_name: str,
        running_check_cmd: str,
        status_all_cmd: str,
    ) -> tuple[bool, str, str, str, int]:
        timeout_sec = self._completion_timeout_sec()
        poll_interval_sec = self._completion_poll_interval_sec()
        deadline = time.time() + timeout_sec
        polls = 0
        last_running_status = ""
        last_status_all = ""
        completion_state = "timeout"

        while time.time() < deadline:
            polls += 1
            running_check_result = ssh_client.execute_command(running_check_cmd, timeout=15)
            last_running_status = running_check_result.stdout or running_check_result.stderr or ""
            is_running = running_check_result.success and self._is_container_running(container_name, running_check_result.stdout)

            status_all_result = ssh_client.execute_command(status_all_cmd, timeout=15)
            last_status_all = status_all_result.stdout or status_all_result.stderr or ""
            status_line = self._status_line_for_container(container_name, last_status_all)

            if not is_running:
                if "Exited (0)" in status_line:
                    completion_state = "exited_0"
                    return True, completion_state, last_running_status, last_status_all, polls
                if "Exited (" in status_line and "Exited (0)" not in status_line:
                    completion_state = "exited_non_zero"
                    return False, completion_state, last_running_status, last_status_all, polls
                # Non-running and no explicit failure status: treat as completed.
                completion_state = "not_running"
                return True, completion_state, last_running_status, last_status_all, polls

            time.sleep(poll_interval_sec)

        return False, completion_state, last_running_status, last_status_all, polls

    def execute_impl(self) -> ActionExecutionResult:
        vm = self.ctx.get_target_vm()
        preset = self._resolve_runtime_preset()
        preset_parameters = preset.get("parameters")
        if isinstance(preset_parameters, dict):
            self.ctx.action["__preset_parameters"] = preset_parameters
        target = str(self.ctx.action.get("target"))
        requested_preset = _normalize_profiling_preset(str(self.ctx.require_action_field("preset")))
        if self._cleanup_only_requested():
            return self._execute_cleanup_only(
                vm=vm,
                target=target,
                requested_preset=requested_preset,
            )
        command_template = str(preset.get("command") or "")
        if not command_template.strip():
            raise ActionExecutionError(f"Runtime preset '{preset.get('name')}' has an empty command.")

        container_name, rendered_command, final_command, normalized_legacy_validate_dir = self._render_probe_command(
            preset_name=requested_preset,
            command_template=command_template,
        )
        timeout_sec = int(self.ctx.action.get("timeout_sec") or 45)
        ssh_client = self._build_ssh_client(vm)
        command_result = ssh_client.execute_command(final_command, timeout=timeout_sec)

        running_check_cmd = (
            f"docker ps --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )
        running_check_result = ssh_client.execute_command(running_check_cmd, timeout=15)
        is_running = (
            command_result.success
            and running_check_result.success
            and self._is_container_running(container_name, running_check_result.stdout)
        )
        status_all_cmd = (
            f"docker ps -a --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )
        container_status_all = ""
        container_logs_tail = ""
        sender_completion = {
            "expected": self._is_sender_preset(requested_preset),
            "completed": False,
            "state": "not_required",
            "polls": 0,
        }
        probe_startup_wait_sec = self._probe_startup_wait_sec()
        probe_poll_interval_sec = self._completion_poll_interval_sec()
        probe_completion_timeout_sec = self._completion_timeout_sec()
        sender_running_after_startup = False
        responder_running_after_startup = False
        sender_startup_status = ""
        responder_startup_status = ""
        cloud_ssh_client = None
        if not command_result.success:
            status_all_result = ssh_client.execute_command(status_all_cmd, timeout=15)
            container_status_all = status_all_result.stdout
            logs_result = ssh_client.execute_command(
                f"docker logs --tail 80 {container_name}",
                timeout=20,
            )
            container_logs_tail = logs_result.stdout or logs_result.stderr

        success = bool(command_result.success)
        if success and sender_completion["expected"]:
            if probe_startup_wait_sec > 0:
                time.sleep(probe_startup_wait_sec)

            sender_running_after_startup, sender_startup_status = self._container_running_snapshot(
                ssh_client=ssh_client,
                container_name=container_name,
            )
            responder_container_name, _ = self._resolve_container_names("profiling/latency_responder")
            responder_running_after_startup = False
            responder_startup_status = ""
            cloud_vm = self._resolve_cloud_vm(target)
            if cloud_vm:
                cloud_ssh_client = self._build_ssh_client(cloud_vm)
                responder_running_after_startup, responder_startup_status = self._container_running_snapshot(
                    ssh_client=cloud_ssh_client,
                    container_name=responder_container_name,
                )

            sender_completion["state"] = (
                "running"
                if sender_running_after_startup and responder_running_after_startup
                else "startup_check_partial"
            )

            sender_ok, sender_state, running_status, status_all, sender_polls = self._wait_sender_completion(
                ssh_client=ssh_client,
                container_name=container_name,
                running_check_cmd=running_check_cmd,
                status_all_cmd=status_all_cmd,
            )
            sender_completion["completed"] = sender_ok
            sender_completion["state"] = sender_state
            sender_completion["polls"] = sender_polls
            success = sender_ok
            if running_status:
                running_check_result = type("SenderCompletionResult", (), {"stdout": running_status})()
            container_status_all = status_all
            if not sender_ok:
                logs_result = ssh_client.execute_command(
                    f"docker logs --tail 80 {container_name}",
                    timeout=20,
                )
                container_logs_tail = logs_result.stdout or logs_result.stderr

        status = "SUCCESS" if success else "FAILED"
        return ActionExecutionResult(
            action_type=self.action_type,
            target=target,
            success=success,
            status=status,
            message=(
                (
                    f"Zenoh profiling preset '{requested_preset}' completed test in container '{container_name}'."
                    if sender_completion["expected"]
                    else f"Zenoh profiling preset '{requested_preset}' started container '{container_name}'."
                )
                if success
                else f"Zenoh profiling preset '{requested_preset}' failed to execute."
            ),
            logs=[command_result.stdout] if command_result.stdout else [],
            debug={
                "requested_preset": requested_preset,
                "resolved_preset": {
                    "group": preset.get("group", ""),
                    "name": preset.get("name", ""),
                    "executor": preset.get("executor", ""),
                },
                "container_name": container_name,
                "command_template": command_template,
                "command_rendered": rendered_command,
                "command_final": final_command,
                "legacy_validate_dir_rewritten": normalized_legacy_validate_dir,
                "running_check_cmd": running_check_cmd,
                "computed_cloud_router_ep": self._resolve_cloud_router_endpoint(target),
                "vm": {
                    "host": vm.get("address", ""),
                    "user": vm.get("user_name", ""),
                    "port": vm.get("port", 22),
                },
                "returncode": command_result.returncode,
                "stderr": command_result.stderr,
                "container_running": is_running,
                "container_status_running": running_check_result.stdout,
                "container_status_all": container_status_all,
                "container_logs_tail": container_logs_tail,
                "probe_startup_wait_sec": probe_startup_wait_sec,
                "probe_completion_poll_interval_sec": probe_poll_interval_sec,
                "probe_completion_timeout_sec": probe_completion_timeout_sec,
                "probe_sender_running_after_startup": sender_running_after_startup if sender_completion["expected"] else False,
                "probe_responder_running_after_startup": responder_running_after_startup if sender_completion["expected"] else False,
                "probe_sender_startup_status": sender_startup_status if sender_completion["expected"] else "",
                "probe_responder_startup_status": responder_startup_status if sender_completion["expected"] else "",
                "probe_startup_both_containers_running": (
                    bool(sender_running_after_startup and responder_running_after_startup)
                    if sender_completion["expected"]
                    else False
                ),
                "sender_completion_expected": sender_completion["expected"],
                "sender_completed": sender_completion["completed"],
                "sender_completion_state": sender_completion["state"],
                "sender_completion_polls": sender_completion["polls"],
            },
        )

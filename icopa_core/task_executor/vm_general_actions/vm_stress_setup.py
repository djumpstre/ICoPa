"""Stress workload executor for VM runtime presets."""

from __future__ import annotations

import math
import re
import time
from typing import Any

from icopa_core.connectors.vm_ssh_client import VMSSHClient

from ..base_exec import ActionExecutionError, ActionExecutionResult
from ..vm_exec import VMProfilingBaseExec

STRESS_CONTAINER_CHECK_INTERVAL_SEC = 3
STRESS_CONTAINER_CHECK_TIMEOUT_SEC = 60
STRESS_WORKLOAD_CAPACITY_LIMIT_RATIO = 0.8

_DOCKER_CONTAINER_NAME_PATTERN = re.compile(r"--name\s+([A-Za-z0-9_.-]+)")


def _as_positive_int_with_default(raw_value: Any, default_value: int) -> int:
    try:
        parsed = int(str(raw_value))
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


def _as_truthy(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}


class VMStressContainerSetupExec(VMProfilingBaseExec):
    """Run stress presets and verify container startup state."""

    action_type = "run_runtime_preset:stress"

    def validate_input(self) -> None:
        super().validate_input()
        preset = str(self.ctx.require_action_field("preset"))
        if not preset.startswith("stress/"):
            raise ActionExecutionError("VMStressContainerSetupExec expects preset in 'stress/*'.")

    def _is_cleanup_only(self, debug_payload: dict[str, Any]) -> bool:
        action_parameters = self.ctx.action.get("parameters")
        if isinstance(action_parameters, dict) and _as_truthy(action_parameters.get("cleanup_only")):
            return True
        stress_params = debug_payload.get("stress_effective_parameters")
        if isinstance(stress_params, dict) and _as_truthy(stress_params.get("cleanup_only")):
            return True
        return False

    @staticmethod
    def _extract_container_name(debug_payload: dict[str, Any]) -> str:
        explicit_name = str(debug_payload.get("stress_container_name") or "").strip()
        if explicit_name:
            return explicit_name
        command_text = str(debug_payload.get("command") or "").strip()
        if not command_text:
            return ""
        match = _DOCKER_CONTAINER_NAME_PATTERN.search(command_text)
        return match.group(1) if match else ""

    @staticmethod
    def _running_status_command(container_name: str) -> str:
        return (
            f"docker ps --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )

    @staticmethod
    def _all_status_command(container_name: str) -> str:
        return (
            f"docker ps -a --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )

    def _wait_until_running(
        self,
        ssh_client: VMSSHClient,
        container_name: str,
        timeout_sec: int,
    ) -> tuple[bool, str, int]:
        check_cmd = self._running_status_command(container_name)
        interval_sec = max(1, int(STRESS_CONTAINER_CHECK_INTERVAL_SEC))
        max_attempts = max(1, math.ceil(timeout_sec / interval_sec))
        last_status = ""

        for attempt in range(max_attempts):
            check_result = ssh_client.execute_command(check_cmd, timeout=15)
            last_status = check_result.stdout or check_result.stderr or ""
            if check_result.success and container_name in (check_result.stdout or ""):
                return True, last_status, attempt + 1
            if attempt < max_attempts - 1:
                time.sleep(interval_sec)
        return False, last_status, max_attempts

    def _check_already_running(
        self,
        ssh_client: VMSSHClient,
        container_name: str,
    ) -> tuple[bool, str, str]:
        check_cmd = self._running_status_command(container_name)
        check_result = ssh_client.execute_command(check_cmd, timeout=15)
        status_text = check_result.stdout or check_result.stderr or ""
        is_running = check_result.success and container_name in (check_result.stdout or "")
        return is_running, status_text, check_cmd

    def _resolve_stress_capabilities(self) -> dict[str, Any]:
        action_capabilities = self.ctx.action.get("__vm_capabilities")
        if isinstance(action_capabilities, dict) and action_capabilities:
            return action_capabilities
        vm = self.ctx.get_target_vm()
        metadata = vm.get("metadata") if isinstance(vm, dict) else {}
        if isinstance(metadata, dict):
            cached = metadata.get("capabilities")
            if isinstance(cached, dict) and cached:
                return cached
        return {}

    def _resolve_requested_stress_resources(self, preset: dict[str, Any]) -> tuple[int | None, int | None]:
        preset_params = preset.get("stress_effective_parameters")
        if isinstance(preset_params, dict):
            cpu_value = preset_params.get("cpu_cores")
            mem_value = preset_params.get("mem_gb")
        else:
            action_params = self.ctx.action.get("parameters")
            cpu_value = action_params.get("cpu_cores") if isinstance(action_params, dict) else None
            mem_value = action_params.get("mem_gb") if isinstance(action_params, dict) else None
        try:
            cpu_cores = int(cpu_value) if cpu_value not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("Field 'cpu_cores' must be a positive integer.") from exc
        try:
            mem_gb = int(mem_value) if mem_value not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("Field 'mem_gb' must be a positive integer.") from exc
        if cpu_cores is not None and cpu_cores <= 0:
            raise ActionExecutionError("Field 'cpu_cores' must be a positive integer.")
        if mem_gb is not None and mem_gb <= 0:
            raise ActionExecutionError("Field 'mem_gb' must be a positive integer.")
        return cpu_cores, mem_gb

    def _validate_requested_workload_vs_capacity(self, preset: dict[str, Any]) -> None:
        stress_params = preset.get("stress_effective_parameters")
        if isinstance(stress_params, dict) and _as_truthy(stress_params.get("cleanup_only")):
            return
        action_parameters = self.ctx.action.get("parameters")
        if isinstance(action_parameters, dict) and _as_truthy(action_parameters.get("cleanup_only")):
            return

        requested_cpu_cores, requested_mem_gb = self._resolve_requested_stress_resources(preset)
        if requested_cpu_cores is None and requested_mem_gb is None:
            return

        capabilities = self._resolve_stress_capabilities()
        if not capabilities:
            raise ActionExecutionError(
                "Missing VM capabilities for stress safety validation. Run VM capability discovery first."
            )

        try:
            detected_cpu_cores = int(capabilities.get("cpu_cores"))
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("Detected VM capability 'cpu_cores' is invalid.") from exc
        cpu_limit = max(1, math.floor(detected_cpu_cores * STRESS_WORKLOAD_CAPACITY_LIMIT_RATIO))
        if requested_cpu_cores is not None and requested_cpu_cores > cpu_limit:
            raise ActionExecutionError(
                f"Requested cpu_cores={requested_cpu_cores} exceeds cpu safety limit {cpu_limit} "
                f"(80% of detected {detected_cpu_cores} cores)."
            )

        try:
            detected_mem_total_gb = float(capabilities.get("mem_total_gb"))
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("Detected VM capability 'mem_total_gb' is invalid.") from exc
        mem_limit = max(1, math.floor(detected_mem_total_gb * STRESS_WORKLOAD_CAPACITY_LIMIT_RATIO))
        if requested_mem_gb is not None and requested_mem_gb > mem_limit:
            raise ActionExecutionError(
                f"Requested mem_gb={requested_mem_gb} exceeds memory safety limit {mem_limit} GiB "
                f"(80% of detected {detected_mem_total_gb:.2f} GiB)."
            )

    def _execute_stress_command(self, preset: dict[str, Any]) -> ActionExecutionResult:
        vm = self.ctx.get_target_vm()
        target = str(self.ctx.action.get("target"))
        command = str(preset.get("command") or "")
        if not command.strip():
            raise ActionExecutionError(f"Runtime preset '{preset.get('name')}' has an empty command.")
        rendered_command = command if bool(preset.get("skip_template_render")) else self._render_command(command)
        timeout_sec = int(self.ctx.action.get("timeout_sec") or 30)
        ssh_client = self._build_ssh_client(vm)
        command_result = ssh_client.execute_command(rendered_command, timeout=timeout_sec)
        status = "SUCCESS" if command_result.success else "FAILED"
        return ActionExecutionResult(
            action_type=self.action_type,
            target=target,
            success=command_result.success,
            status=status,
            message=(
                f"Runtime preset '{preset.get('name')}' executed."
                if command_result.success
                else f"Runtime preset '{preset.get('name')}' failed."
            ),
            logs=[command_result.stdout] if command_result.stdout else [],
            debug={
                "preset": {
                    "group": preset.get("group", ""),
                    "name": preset.get("name", ""),
                    "executor": preset.get("executor", ""),
                },
                "preset_source": preset.get("source", "runtime_env"),
                "command": rendered_command,
                "vm": {
                    "host": vm.get("address", ""),
                    "user": vm.get("user_name", ""),
                    "port": vm.get("port", 22),
                },
                "returncode": command_result.returncode,
                "stderr": command_result.stderr,
                "resolved_static_name": preset.get("resolved_static_name"),
                "resolved_static_id": preset.get("resolved_static_id"),
                "stress_container_name": preset.get("stress_container_name"),
                "stress_effective_parameters": preset.get("stress_effective_parameters"),
            },
        )

    def execute_impl(self) -> ActionExecutionResult:
        preset = self._resolve_runtime_preset()
        self._validate_requested_workload_vs_capacity(preset)
        target = str(self.ctx.action.get("target"))
        preset_name = str(self.ctx.action.get("preset") or preset.get("name") or "stress")
        stress_parameters = (
            preset.get("stress_effective_parameters")
            if isinstance(preset.get("stress_effective_parameters"), dict)
            else {}
        )
        container_name = str(preset.get("stress_container_name") or "").strip()
        cleanup_only = _as_truthy(stress_parameters.get("cleanup_only")) or _as_truthy(
            (self.ctx.action.get("parameters") or {}).get("cleanup_only")
            if isinstance(self.ctx.action.get("parameters"), dict)
            else None
        )

        if not cleanup_only and container_name:
            vm = self.ctx.get_target_vm()
            ssh_client = self._build_ssh_client(vm)
            already_running, status_text, check_cmd = self._check_already_running(
                ssh_client=ssh_client,
                container_name=container_name,
            )
            if already_running:
                return ActionExecutionResult(
                    action_type=self.action_type,
                    target=target,
                    success=True,
                    status="SUCCESS",
                    message=(
                        f"Stress preset '{preset_name}' skipped redeploy: container "
                        f"'{container_name}' is already running."
                    ),
                    debug={
                        "preset": {
                            "group": preset.get("group", ""),
                            "name": preset.get("name", ""),
                            "executor": preset.get("executor", ""),
                        },
                        "preset_source": preset.get("source", "runtime_env"),
                        "command": check_cmd,
                        "vm": {
                            "host": vm.get("address", ""),
                            "user": vm.get("user_name", ""),
                            "port": vm.get("port", 22),
                        },
                        "returncode": 0,
                        "stderr": "",
                        "resolved_static_name": preset.get("resolved_static_name"),
                        "resolved_static_id": preset.get("resolved_static_id"),
                        "stress_container_name": container_name,
                        "stress_effective_parameters": stress_parameters,
                        "container_running": True,
                        "container_status_running": status_text,
                        "managed_containers_running": [container_name],
                        "skipped_existing_running": True,
                    },
                )

        result = self._execute_stress_command(preset)
        debug_payload = dict(result.debug or {})
        container_name = self._extract_container_name(debug_payload)
        cleanup_only = self._is_cleanup_only(debug_payload)
        result.action_type = self.action_type

        if cleanup_only:
            if container_name:
                debug_payload["managed_containers_removed"] = [container_name]
            result.debug = debug_payload
            return result

        if not result.success:
            result.debug = debug_payload
            return result
        if not container_name:
            result.success = False
            result.status = "FAILED"
            result.message = (
                f"Stress preset '{self.ctx.action.get('preset')}' must run a named docker container "
                "so startup health checks can be performed."
            )
            result.debug = debug_payload
            return result

        vm = self.ctx.get_target_vm()
        ssh_client = self._build_ssh_client(vm)
        timeout_sec = _as_positive_int_with_default(
            self.ctx.action.get("container_check_timeout_sec"),
            STRESS_CONTAINER_CHECK_TIMEOUT_SEC,
        )
        running_ok, running_status, attempts = self._wait_until_running(
            ssh_client=ssh_client,
            container_name=container_name,
            timeout_sec=timeout_sec,
        )

        debug_payload["container_check_interval_sec"] = STRESS_CONTAINER_CHECK_INTERVAL_SEC
        debug_payload["container_check_timeout_sec"] = timeout_sec
        debug_payload["container_check_attempts"] = attempts
        debug_payload["container_running"] = running_ok
        debug_payload["container_status_running"] = running_status

        if running_ok:
            debug_payload["managed_containers_running"] = [container_name]
            debug_payload["managed_containers_created"] = [container_name]
            result.message = (
                f"Stress preset '{self.ctx.action.get('preset')}' started and container "
                f"'{container_name}' is running."
            )
            result.debug = debug_payload
            return result

        status_all_result = ssh_client.execute_command(
            self._all_status_command(container_name),
            timeout=15,
        )
        logs_result = ssh_client.execute_command(
            f"docker logs --tail 80 {container_name}",
            timeout=20,
        )
        debug_payload["container_status_all"] = status_all_result.stdout or status_all_result.stderr or ""
        debug_payload["container_logs_tail"] = logs_result.stdout or logs_result.stderr or ""
        result.success = False
        result.status = "FAILED"
        result.message = (
            f"Stress preset '{self.ctx.action.get('preset')}' started command successfully, "
            f"but container '{container_name}' did not reach running state within {timeout_sec}s."
        )
        result.debug = debug_payload
        return result

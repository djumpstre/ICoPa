"""VM-based execution for profiling actions."""

from __future__ import annotations

import base64
from copy import deepcopy
import math
import re
import shlex
import hashlib
from pathlib import Path
from typing import Any

import yaml

from icopa_core.connectors.vm_ssh_client import VMSSHClient, build_vm_ssh_client
from icopa_core.task_executor.vm_scp_collect_metrics import SCPCollectTarget, collect_metrics_from_vms

from .base_exec import ActionExecutionError, ActionExecutionResult, ProfilingBaseExec

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DEFAULT_COMMAND_TIMEOUT_SEC = 30
_DOCKER_NAME_SAFE_PATTERN = re.compile(r"[^a-z0-9_.-]+")


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _render_rrt_config_yaml(
    *,
    runtime_yaml_text: str,
    overrides: dict[str, Any] | None,
) -> str:
    raw_yaml = str(runtime_yaml_text or "").strip()
    override_payload = overrides if isinstance(overrides, dict) else {}
    if not raw_yaml and not override_payload:
        return ""

    base_payload: dict[str, Any] = {}
    if raw_yaml:
        loaded = yaml.safe_load(raw_yaml)
        if loaded is None:
            base_payload = {}
        elif isinstance(loaded, dict):
            base_payload = loaded
        else:
            raise ActionExecutionError("runtime_env.serializer_rrt_config_raw_yaml must be a YAML object.")

    merged_payload = _deep_merge_dict(base_payload, override_payload) if override_payload else base_payload
    return yaml.safe_dump(merged_payload, sort_keys=False)


def _build_ssh_client_from_vm(vm: dict[str, Any], action: dict[str, Any], workdir: str) -> VMSSHClient:
    try:
        client = build_vm_ssh_client(vm, action=action, workdir=workdir, default_connect_timeout=8)
    except ValueError as exc:
        raise ActionExecutionError(str(exc)) from exc
    if not client.key_path or not str(client.key_path).strip():
        raise ActionExecutionError("Target VM does not include an SSH key path.")
    if not Path(str(client.key_path)).exists():
        raise ActionExecutionError(
            f"SSH key path does not exist on executor host: {client.key_path}"
        )
    return client


def _as_positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError(f"'{field}' must be a positive integer.") from exc
    if parsed <= 0:
        raise ActionExecutionError(f"'{field}' must be a positive integer.")
    return parsed


def _as_optional_positive_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    return _as_positive_int(value, field)


def _as_optional_cpu_load(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError(f"'{field}' must be an integer between 0 and 100.") from exc
    if parsed < 0 or parsed > 100:
        raise ActionExecutionError(f"'{field}' must be an integer between 0 and 100.")
    return parsed


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _sanitize_docker_name(text: str) -> str:
    normalized = _DOCKER_NAME_SAFE_PATTERN.sub("-", str(text).strip().lower()).strip("-.")
    return normalized or "x"


def _scenario_name_from_payload(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    if isinstance(metadata, dict) and metadata.get("name"):
        return str(metadata.get("name"))
    if isinstance(payload, dict) and payload.get("name"):
        return str(payload.get("name"))
    return "scenario"


def _compose_stress_container_name(scenario_name: str, action_type: str, target: str) -> str:
    base = "-".join(
        [
            "icopa",
            _sanitize_docker_name(scenario_name),
            _sanitize_docker_name(action_type),
            _sanitize_docker_name(target),
        ]
    )
    if len(base) <= 128:
        return base
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    trimmed = base[:119].rstrip("-.")
    return f"{trimmed}-{digest}"


def _resolve_static_stress_preset_container(requested_name: str):
    from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import (
        PresetContainersError,
        resolve_preset_container,
    )

    try:
        preset = resolve_preset_container(requested_name)
        if preset is not None:
            preset.require_image()
        return preset
    except PresetContainersError as exc:
        raise ActionExecutionError(str(exc)) from exc


def _is_static_stress_fallback_enabled() -> bool:
    from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import (
        is_preset_containers_fallback_enabled,
    )

    return bool(is_preset_containers_fallback_enabled())


class VMConnectCheckExec(ProfilingBaseExec):
    """Connectivity checks over SSH."""

    action_type = "check_connectivity"

    def validate_input(self) -> None:
        self.ctx.get_target_vm()

    def execute_impl(self) -> ActionExecutionResult:
        vm = self.ctx.get_target_vm()
        target = str(self.ctx.action.get("target"))
        parameters = self.ctx.action.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        ssh_client = _build_ssh_client_from_vm(vm, self.ctx.action, self.ctx.workdir)
        ssh_result = ssh_client.test_connectivity(
            check_container_runtime=bool(parameters.get("check_container_runtime", True))
        )

        status = "SUCCESS" if ssh_result.success else "FAILED"
        return ActionExecutionResult(
            action_type=self.action_type,
            target=target,
            success=ssh_result.success,
            status=status,
            message="Connectivity check completed." if ssh_result.success else "Connectivity check failed.",
            logs=[ssh_result.stdout] if ssh_result.stdout else [],
            debug={
                "host": vm.get("address", ""),
                "user": vm.get("user_name", ""),
                "port": vm.get("port", 22),
                "returncode": ssh_result.returncode,
                "stderr": ssh_result.stderr,
            },
        )


class VMExecBase(ProfilingBaseExec):
    """Shared helpers for VM-based actions."""

    def _build_ssh_client(self, vm: dict[str, Any]) -> VMSSHClient:
        return _build_ssh_client_from_vm(vm, self.ctx.action, self.ctx.workdir)

    @staticmethod
    def _camel_to_snake(name: str) -> str:
        out = []
        for idx, char in enumerate(name):
            if char.isupper() and idx > 0:
                out.append("_")
            out.append(char.lower())
        return "".join(out)

    @classmethod
    def _normalize_keys(cls, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            normalized[key_str] = value
            normalized.setdefault(cls._camel_to_snake(key_str), value)
        return normalized

    def _build_template_values(self) -> dict[str, Any]:
        runtime_parameters = self.ctx.runtime_env.get("parameters") or {}
        runtime_images = self.ctx.runtime_env.get("images") or {}
        if not isinstance(runtime_parameters, dict):
            runtime_parameters = {}
        if not isinstance(runtime_images, dict):
            runtime_images = {}

        values: dict[str, Any] = {}
        values.update(self._normalize_keys(runtime_parameters))
        values.update(self._normalize_keys(runtime_images))

        preset_parameters = self.ctx.action.get("__preset_parameters")
        if isinstance(preset_parameters, dict):
            values.update(self._normalize_keys(preset_parameters))

        runner_image = runtime_images.get("runnerImage")
        if runner_image:
            values.setdefault("runner_image", runner_image)
            values.setdefault("runnerImage", runner_image)

        overrides = self.ctx.action.get("parameters") or {}
        if isinstance(overrides, dict):
            values.update(self._normalize_keys(overrides))

        provided_rrt_yaml = str(overrides.get("rrt_config_yaml") or "").strip() if isinstance(overrides, dict) else ""
        provided_rrt_yaml_b64 = str(overrides.get("rrt_config_yaml_b64") or "").strip() if isinstance(overrides, dict) else ""
        if provided_rrt_yaml:
            effective_rrt_yaml = provided_rrt_yaml
        else:
            rrt_overrides = overrides.get("rrt_config_overrides") if isinstance(overrides, dict) else None
            runtime_rrt_yaml = str(self.ctx.runtime_env.get("serializer_rrt_config_raw_yaml") or "")
            effective_rrt_yaml = _render_rrt_config_yaml(
                runtime_yaml_text=runtime_rrt_yaml,
                overrides=rrt_overrides if isinstance(rrt_overrides, dict) else None,
            )
        values.setdefault("rrt_config_file_path", "/workspace/config/rrt_config.yaml")
        values.setdefault("rrt_config_yaml_b64", "")
        values.setdefault("icopa_metrics_dir", "/tmp/icopa/default-metrics")
        values.setdefault("validate_metrics_dir", values.get("icopa_metrics_dir"))
        values.setdefault("validate_metrics_path", values.get("validate_metrics_dir"))
        if effective_rrt_yaml:
            values["rrt_config_yaml"] = effective_rrt_yaml
            values["rrt_config_yaml_b64"] = (
                provided_rrt_yaml_b64
                if provided_rrt_yaml_b64
                else base64.b64encode(effective_rrt_yaml.encode("utf-8")).decode("ascii")
            )
        return values

    def _render_command(self, template: str) -> str:
        values = self._build_template_values()
        referenced = set(_VAR_PATTERN.findall(template))
        for name in referenced & {"runner_image", "runnerImage"}:
            image = values.get(name)
            if not isinstance(image, str) or not image.strip():
                raise ActionExecutionError(
                    "Set spec.images.runnerImage to your container image reference "
                    "before running container presets."
                )
        missing = sorted({name for name in referenced if name not in values})
        if missing:
            missing_text = ", ".join(missing)
            raise ActionExecutionError(
                f"Command template references missing parameter(s): {missing_text}."
            )
        return _VAR_PATTERN.sub(lambda m: str(values[m.group(1)]), template)


class VMProfilingBaseExec(VMExecBase):
    """Base class for VM runtime preset execution actions."""

    action_type = "run_runtime_preset"

    def validate_input(self) -> None:
        self.ctx.get_target_vm()
        self.ctx.require_action_field("preset")

    def _build_static_stress_preset(self, requested_name: str) -> dict[str, Any]:
        container_preset = _resolve_static_stress_preset_container(requested_name)
        if container_preset is None:
            raise ActionExecutionError(f"Static stress preset '{requested_name}' not found.")

        defaults = self._normalize_keys(container_preset.parameters)
        overrides = self.ctx.action.get("parameters") or {}
        if not isinstance(overrides, dict):
            raise ActionExecutionError("Action field 'parameters' must be an object when provided.")
        merged_params = {**defaults, **self._normalize_keys(overrides)}
        cleanup_only = _as_bool(merged_params.get("cleanup_only"))

        scenario_name = _scenario_name_from_payload(self.ctx.scenario)
        target = str(self.ctx.action.get("target") or "vm")
        action_type = str(self.ctx.action.get("type") or "action")
        container_name = _compose_stress_container_name(
            scenario_name=scenario_name,
            action_type=action_type,
            target=target,
        )

        if cleanup_only:
            command = f"docker rm -f {shlex.quote(container_name)} >/dev/null 2>&1 || true"
            return {
                "name": requested_name,
                "group": "stress",
                "executor": "ssh",
                "command": command,
                "source": "preset_containers_fallback",
                "skip_template_render": True,
                "resolved_static_name": container_preset.name,
                "resolved_static_id": container_preset.preset_id,
                "stress_container_name": container_name,
                "stress_effective_parameters": {
                    "cleanup_only": True,
                },
            }

        cpu_cores = _as_positive_int(merged_params.get("cpu_cores"), "cpu_cores")
        mem_gb = _as_positive_int(merged_params.get("mem_gb"), "mem_gb")
        duration_sec = _as_optional_positive_int(merged_params.get("duration_sec"), "duration_sec")
        cpu_load = _as_optional_cpu_load(merged_params.get("cpu_load"), "cpu_load")

        capabilities = self.ctx.action.get("__vm_capabilities")
        if isinstance(capabilities, dict):
            cpu_limit_raw = capabilities.get("cpu_cores")
            if cpu_limit_raw not in (None, ""):
                detected_cpu_cores = _as_positive_int(cpu_limit_raw, "capabilities.cpu_cores")
                cpu_limit = max(1, math.floor(detected_cpu_cores * 0.8))
                if cpu_cores > cpu_limit:
                    raise ActionExecutionError(
                        f"Requested cpu_cores={cpu_cores} exceeds cpu safety limit {cpu_limit} "
                        f"(80% of detected {detected_cpu_cores} cores)."
                    )
            mem_total_raw = capabilities.get("mem_total_gb")
            if mem_total_raw not in (None, ""):
                try:
                    mem_total_gb = float(mem_total_raw)
                except (TypeError, ValueError) as exc:
                    raise ActionExecutionError(
                        "Detected VM memory value 'mem_total_gb' is not numeric."
                    ) from exc
                if mem_total_gb > 0:
                    mem_limit_gb = max(1, math.floor(mem_total_gb * 0.8))
                    if mem_gb > mem_limit_gb:
                        raise ActionExecutionError(
                            f"Requested mem_gb={mem_gb} exceeds safety limit {mem_limit_gb} GiB "
                            f"(80% of detected {mem_total_gb:.2f} GiB)."
                        )

        stress_args = [
            "--cpu-cores",
            str(cpu_cores),
            "--mem-gb",
            str(mem_gb),
        ]
        if duration_sec is not None:
            stress_args.extend(["--duration-sec", str(duration_sec)])
        if cpu_load is not None:
            stress_args.extend(["--cpu-load", str(cpu_load)])
        stress_args_segment = " ".join(shlex.quote(item) for item in stress_args)
        run_args_segment = f" {container_preset.run_args.strip()}" if container_preset.run_args.strip() else ""

        command = (
            f"docker rm -f {shlex.quote(container_name)} >/dev/null 2>&1 || true; "
            f"docker run -d --name {shlex.quote(container_name)}{run_args_segment} "
            f"{shlex.quote(container_preset.image)} {stress_args_segment}"
        )
        return {
            "name": requested_name,
            "group": "stress",
            "executor": "ssh",
            "command": command,
            "source": "preset_containers_fallback",
            "skip_template_render": True,
            "resolved_static_name": container_preset.name,
            "resolved_static_id": container_preset.preset_id,
            "stress_container_name": container_name,
            "stress_effective_parameters": {
                "cpu_cores": cpu_cores,
                "mem_gb": mem_gb,
                "duration_sec": duration_sec,
                "cpu_load": cpu_load,
            },
        }

    def _resolve_runtime_preset(self) -> dict[str, Any]:
        requested = str(self.ctx.require_action_field("preset"))
        # supported request formats:
        # - "group/name"
        # - "name" (legacy)
        if "/" in requested:
            requested_group, requested_name = requested.split("/", 1)
        else:
            requested_group, requested_name = "", requested

        for preset in self.ctx.get_runtime_presets():
            name = str(preset.get("name") or "")
            group = str(preset.get("group") or "")
            if group and requested_group and group == requested_group and name == requested_name:
                return {**preset, "source": "runtime_env"}
            if not requested_group and name == requested_name:
                return {**preset, "source": "runtime_env"}
        if requested_group == "stress" and _is_static_stress_fallback_enabled():
            return self._build_static_stress_preset(requested_name)
        raise ActionExecutionError(f"Runtime preset '{requested}' not found in runtime environment.")

    def execute_impl(self) -> ActionExecutionResult:
        vm = self.ctx.get_target_vm()
        preset = self._resolve_runtime_preset()
        preset_parameters = preset.get("parameters")
        if isinstance(preset_parameters, dict):
            self.ctx.action["__preset_parameters"] = preset_parameters
        target = str(self.ctx.action.get("target"))
        command = str(preset.get("command") or "")
        if not command.strip():
            raise ActionExecutionError(f"Runtime preset '{preset.get('name')}' has an empty command.")

        rendered_command = command if bool(preset.get("skip_template_render")) else self._render_command(command)
        timeout_sec = int(self.ctx.action.get("timeout_sec") or _DEFAULT_COMMAND_TIMEOUT_SEC)

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


class VMProfilingProbeExec(VMProfilingBaseExec):
    """Latency probe action template (specialization point for probe logic)."""

    action_type = "run_runtime_preset:profiling"

    def validate_input(self) -> None:
        super().validate_input()
        preset = str(self.ctx.require_action_field("preset"))
        if not preset.startswith("profiling/"):
            raise ActionExecutionError("VMProfilingProbeExec expects preset in 'profiling/*'.")


class VMMetricCollectionExec(VMExecBase):
    """Run metrics/artifact collection commands on VM targets."""

    action_type = "collect_metrics"

    def validate_input(self) -> None:
        target = str(self.ctx.action.get("target") or "").strip()
        if not target:
            raise ActionExecutionError("collect_metrics action requires non-empty field 'target'.")
        if target not in self.ctx.inventory_by_node:
            raise ActionExecutionError(f"Target VM '{target}' not found in inventory_by_node.")

    def execute_impl(self) -> ActionExecutionResult:
        target = str(self.ctx.action.get("target") or "").strip()
        parameters = self.ctx.action.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        remote_dir = str(parameters.get("remote_dir") or self.ctx.action.get("remote_dir") or "").strip()
        local_base_dir = str(
            parameters.get("local_base_dir") or self.ctx.action.get("local_base_dir") or ""
        ).strip()

        explicit_command = str(self.ctx.action.get("command") or "").strip()
        rendered_command = ""
        timeout_sec = int(
            parameters.get("timeout_sec")
            or self.ctx.action.get("timeout_sec")
            or _DEFAULT_COMMAND_TIMEOUT_SEC
        )
        should_run_command = bool(explicit_command) or not (remote_dir and local_base_dir)
        if should_run_command:
            command_template = explicit_command or "hostname && date -u +%Y-%m-%dT%H:%M:%SZ"
            rendered_command = self._render_command(command_template)

        success = True
        logs: list[str] = []
        debug_targets: dict[str, Any] = {}
        if should_run_command:
            vm = self.ctx.inventory_by_node[target]
            ssh_client = self._build_ssh_client(vm)
            result = ssh_client.execute_command(rendered_command, timeout=timeout_sec)
            success = success and result.success
            if result.stdout:
                logs.append(f"[{target}] {result.stdout}")
            debug_targets[target] = {
                "returncode": result.returncode,
                "stderr": result.stderr,
                "success": result.success,
            }

        scp_payload: dict[str, Any] | None = None
        if remote_dir and local_base_dir:
            vm = self.ctx.inventory_by_node[target]
            key_path = str((vm.get("credential") or {}).get("key_path") or "")
            scp_payload = collect_metrics_from_vms(
                targets=[
                    SCPCollectTarget(
                        node_name=target,
                        host=str(vm.get("address") or ""),
                        username=str(vm.get("user_name") or vm.get("username") or ""),
                        port=int(vm.get("port") or 22),
                        key_path=key_path or None,
                    )
                ],
                remote_dir=remote_dir,
                local_base_dir=local_base_dir,
                connect_timeout_sec=int(
                    parameters.get("connect_timeout_sec")
                    or self.ctx.action.get("connect_timeout_sec")
                    or 8
                ),
                timeout_sec=int(
                    parameters.get("scp_timeout_sec")
                    or self.ctx.action.get("scp_timeout_sec")
                    or 45
                )
            )
            success = success and bool(scp_payload.get("ok"))
            if scp_payload.get("local_base_dir"):
                logs.append(f"[collect_metrics] backend metrics path: {scp_payload.get('local_base_dir')}")

        return ActionExecutionResult(
            action_type=self.action_type,
            target=None,
            success=success,
            status="SUCCESS" if success else "FAILED",
            message=(
                f"Metric collection finished for target '{target}'."
                if success
                else f"Metric collection failed for target '{target}'."
            ),
            logs=logs,
            debug={
                "target": target,
                "command": rendered_command,
                "results_by_target": debug_targets,
                "artifact_pull": "scp" if scp_payload is not None else "none",
                "metrics_remote_dir": remote_dir,
                "metrics_local_base_dir": local_base_dir,
                "artifact_pull_result": scp_payload if isinstance(scp_payload, dict) else {},
            },
        )

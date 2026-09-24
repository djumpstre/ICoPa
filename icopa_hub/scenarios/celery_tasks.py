"""Celery tasks for scenario validation."""

from __future__ import annotations

import logging
import math
import re
import time
from pathlib import Path
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from inventory.models import SSHAccessHistory, VM
from icopa_core.task_executor.action_center import (
    ActionCenterError,
    normalize_action_payload,
)
from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import (
    is_preset_containers_fallback_enabled,
    resolve_preset_container,
)
from icopa_core.connectors.vm_ssh_client import VMSSHClient, build_vm_ssh_client
from icopa_core.task_executor import (
    ActionExecutionError,
    ExecutionContext,
    GraphPingCheckExec,
    VMConnectCheckExec,
    build_executor,
)
from icopa_core.task_executor.vm_runtime_actions.vm_zenoh_routing_setup import (
    builtin_zenoh_routing_preset_names,
)
from icopa_core.task_executor.vm_scp_collect_metrics import SCPCollectTarget, VMScpCollectMetricsExec
from runtime_env.models import RuntimeEnvironment

from .models import Scenario, ScenarioValidationRun, ScenarioValidationRunVM

logger = logging.getLogger(__name__)

PHASE_TASK_SOFT_LIMIT_SEC = 900
PHASE_TASK_HARD_LIMIT_SEC = 1020
NODE_TASK_SOFT_LIMIT_SEC = 300
NODE_TASK_HARD_LIMIT_SEC = 360
GRAPH_TASK_SOFT_LIMIT_SEC = 120
GRAPH_TASK_HARD_LIMIT_SEC = 180
VALIDATE_TASK_SOFT_LIMIT_SEC = 2400
VALIDATE_TASK_HARD_LIMIT_SEC = 2700
GRAPH_PING_DURATION_SEC_DEFAULT = 5
GRAPH_PING_STEP_SEC_DEFAULT = 1.0
GRAPH_PING_WAIT_SEC_DEFAULT = 2
GRAPH_PING_TIMEOUT_SEC_DEFAULT = 20
GRAPH_PING_ESTIMATED_OVERHEAD_SEC = 1.0
_CAPABILITIES_COLLECTOR_VERSION = "v1"
_MAX_HISTORY_TEXT_LEN = 4096
_PROBE_PHASE_NAME = "probe"
_COLLECT_PHASE_NAMES = {"collect_metrics"}
_PROBE_METRICS_REMOTE_BASE_DIR = "/tmp/netanalyzer"
_PROBE_METRICS_STATIC_SUBDIR = ("scenarios", "validattion", "metrics")
_PROGRESS_MAX_VALUE_LEN = 240
_PROGRESS_MAX_LIST_ITEMS = 5
_SENSITIVE_META_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "key_path",
    "private_key",
    "ssh_key",
    "authorization",
)


def _classify_step(step: str) -> str:
    if step.startswith("node_"):
        return "vm-connectivity-check"
    if step.startswith("graph_"):
        return "graph-ping-check"
    if step.startswith("phase_action_"):
        return "phase-action"
    if step.startswith("phase_"):
        return "phase-validation"
    if step in {"start", "completed", "failed", "state_saved_running"}:
        return "orchestrator"
    return "progress"


def _format_progress_message(step: str, meta: dict[str, Any]) -> str:
    key_order = [
        "scenario_name",
        "scenario",
        "scenario_id",
        "phase_name",
        "index",
        "action_type",
        "type",
        "target",
        "node",
        "source",
        "destination",
        "edge_name",
        "edge_index",
        "total_edges",
        "status",
        "ok",
        "success",
        "latency_avg_ms",
        "elapsed_sec",
        "duration_sec",
        "step_sec",
        "wait_sec",
        "timeout_sec",
        "estimated_per_edge_sec",
        "estimated_remaining_sec",
        "estimated_total_sec",
        "preset",
        "message",
        "error_count",
        "log_count",
        "debug_keys",
        "executor",
        "runtime_env_name",
        "runtime_preset_count",
        "inventory_nodes",
        "parameter_keys",
        "checked_actions",
        "executed_actions",
        "seconds",
        "task_state",
        "error",
    ]
    parts: list[str] = [f"step={step}"]
    used_keys: set[str] = set()
    for key in key_order:
        if key in meta and meta.get(key) not in (None, "", [], {}):
            parts.append(f"{key}={_compact_progress_meta_value(key, meta.get(key))}")
            used_keys.add(key)
    extra_keys = sorted(key for key in meta.keys() if key not in used_keys and key != "step")
    for key in extra_keys:
        value = meta.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={_compact_progress_meta_value(key, value)}")
    if len(parts) == 1:
        return f"step={step}"
    return " ".join(parts)


def _is_sensitive_meta_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(part in lowered for part in _SENSITIVE_META_KEY_PARTS)


def _compact_progress_text(value: str) -> str:
    collapsed = " ".join(str(value).split())
    if len(collapsed) <= _PROGRESS_MAX_VALUE_LEN:
        return collapsed
    return f"{collapsed[:_PROGRESS_MAX_VALUE_LEN]}...(truncated)"


def _compact_progress_meta_value(key: str, value: Any) -> str:
    if _is_sensitive_meta_key(key):
        return "<redacted>"
    if isinstance(value, str):
        return _compact_progress_text(value)
    if isinstance(value, (bool, int, float)):
        return str(value)
    if value is None:
        return "None"
    if isinstance(value, dict):
        keys = sorted(str(item) for item in value.keys())
        shown = keys[:_PROGRESS_MAX_LIST_ITEMS]
        suffix = ",..." if len(keys) > _PROGRESS_MAX_LIST_ITEMS else ""
        return f"dict(len={len(keys)},keys=[{','.join(shown)}{suffix}])"
    if isinstance(value, list):
        sample = ",".join(_compact_progress_meta_value(key, item) for item in value[:_PROGRESS_MAX_LIST_ITEMS])
        suffix = ",..." if len(value) > _PROGRESS_MAX_LIST_ITEMS else ""
        return f"list(len={len(value)},sample=[{sample}{suffix}])"
    return _compact_progress_text(str(value))


def _emit_progress(task, step: str, **meta: Any) -> None:
    """Emit task progress to logs/stdout for backend debugging."""
    task_id = getattr(getattr(task, "request", None), "id", "") or ""
    payload = {"step": step, **meta}
    task_kind = _classify_step(step)
    line = (
        f"[scenario-validate][task:{task_kind}][id:{task_id}] "
        f"{_format_progress_message(step, payload)}"
    )
    logger.info(line)
    try:
        task.update_state(state="PROGRESS", meta=payload)
    except Exception:
        return


def _get_scenario_spec(scenario: Scenario) -> dict:
    payload = scenario.raw_payload if isinstance(scenario.raw_payload, dict) else {}
    spec = payload.get("spec") if isinstance(payload, dict) else {}
    return spec if isinstance(spec, dict) else {}


def _resolve_edge_context(spec: dict, edge_name: str | None) -> tuple[dict[str, str], str | None]:
    selected_edge_name = str(edge_name or "").strip()
    if not selected_edge_name:
        return {}, None
    graph = spec.get("graph")
    edges = graph.get("edges") if isinstance(graph, dict) and isinstance(graph.get("edges"), list) else []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        current_name = str(edge.get("name") or "").strip()
        if current_name != selected_edge_name:
            continue
        edge_from = str(edge.get("from") or "").strip()
        edge_to = str(edge.get("to") or "").strip()
        if not edge_from or not edge_to:
            return {}, f"edge '{selected_edge_name}' requires non-empty from/to."
        return {"name": selected_edge_name, "from": edge_from, "to": edge_to}, None
    return {}, f"edge '{selected_edge_name}' was not found in spec.graph.edges."


def _render_edge_placeholders(value: Any, edge_context: dict[str, str]) -> Any:
    if not edge_context:
        return value
    if isinstance(value, str):
        rendered = value
        for key in ("name", "from", "to"):
            rendered = rendered.replace(f"${{edge.{key}}}", edge_context.get(key, ""))
        return rendered
    if isinstance(value, list):
        return [_render_edge_placeholders(item, edge_context) for item in value]
    if isinstance(value, dict):
        return {k: _render_edge_placeholders(v, edge_context) for k, v in value.items()}
    return value


def _extract_phase_templates(spec: dict, phase_name: str | None, edge_name: str | None = None) -> tuple[list[dict], list[str]]:
    phase_templates_raw = spec.get("phaseTemplates")
    if spec.get("phases") is not None:
        return [], ["spec.phases is not supported. Use spec.phaseTemplates with actions."]
    if phase_templates_raw is None:
        phase_templates_raw = []
    if not isinstance(phase_templates_raw, list):
        return [], ["spec.phaseTemplates must be a list."]

    errors: list[str] = []
    raw_nodes = spec.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []

    def _normalized_actions_for_phase(raw_actions: Any, phase_label: str) -> list[dict[str, Any]]:
        if raw_actions is None:
            return []
        if not isinstance(raw_actions, list):
            errors.append(f"phaseTemplates[{phase_label}].actions must be a list.")
            return []
        out: list[dict[str, Any]] = []
        for idx, raw_action in enumerate(raw_actions):
            if not isinstance(raw_action, dict):
                errors.append(f"phase '{phase_label}' action[{idx}] must be an object.")
                continue
            try:
                expanded = normalize_action_payload(
                    raw_action=raw_action,
                    nodes=nodes,
                    field_path=f"phaseTemplates[{phase_label}].actions[{idx}]",
                )
            except ActionCenterError as exc:
                errors.append(str(exc))
                continue
            out.extend(expanded)
        return out

    phase_candidates: list[dict[str, Any]] = [phase for phase in phase_templates_raw if isinstance(phase, dict)]
    if phase_name:
        selected = [phase for phase in phase_candidates if str(phase.get("name") or "") == phase_name]
        if not selected and phase_name in _COLLECT_PHASE_NAMES:
            selected = [
                phase
                for phase in phase_candidates
                if str(phase.get("name") or "") in _COLLECT_PHASE_NAMES
            ]
        if not selected:
            return [], [f"Phase '{phase_name}' not found in spec.phaseTemplates."]
        phase_candidates = selected

    edge_context, edge_error = _resolve_edge_context(spec, edge_name)
    if edge_error:
        return [], [edge_error]

    valid_phases: list[dict[str, Any]] = []
    for phase in phase_candidates:
        phase_label = str(phase.get("name") or "unnamed-phase")
        if phase.get("steps") is not None:
            errors.append(f"phaseTemplates[{phase_label}].steps is not supported. Use actions.")
        normalized_actions = _normalized_actions_for_phase(
            _render_edge_placeholders(phase.get("actions"), edge_context),
            phase_label,
        )
        valid_phases.append({**phase, "actions": normalized_actions})

    return valid_phases, errors


def _runtime_preset_name_set(runtime_env_payload: dict[str, Any]) -> set[str]:
    presets = runtime_env_payload.get("command_preset")
    if not isinstance(presets, list):
        return set()
    names: set[str] = set()
    for item in presets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        group = str(item.get("group") or "").strip()
        if name:
            names.add(name)
        if group and name:
            names.add(f"{group}/{name}")
    tags = runtime_env_payload.get("tags")
    zenoh = tags.get("zenoh") if isinstance(tags, dict) else None
    if isinstance(zenoh, dict) and (bool(zenoh) or zenoh.get("enabled") is True):
        names.update(builtin_zenoh_routing_preset_names())
    else:
        names.add("routing/clean_router")
    return names


def _validate_actions(
    spec: dict,
    phase_name: str | None,
    runtime_env_payload: dict[str, Any] | None = None,
    edge_name: str | None = None,
) -> dict:
    errors: list[str] = []
    phase_templates, phase_errors = _extract_phase_templates(spec, phase_name, edge_name=edge_name)
    errors.extend(phase_errors)

    if "actionCatalog" in spec:
        errors.append("spec.actionCatalog is deprecated and no longer supported. Remove it from scenario YAML.")

    runtime_payload = runtime_env_payload if isinstance(runtime_env_payload, dict) else {}
    preset_names = _runtime_preset_name_set(runtime_payload)
    checked_actions = 0
    checked_phase_names: list[str] = []
    for phase in phase_templates:
        phase_label = str(phase.get("name") or "unnamed-phase")
        checked_phase_names.append(phase_label)
        actions = phase.get("actions") or []
        if not isinstance(actions, list):
            errors.append(f"phaseTemplates[{phase_label}].actions must be a list.")
            continue
        for idx, action in enumerate(actions):
            checked_actions += 1
            if not isinstance(action, dict):
                errors.append(f"phase '{phase_label}' action[{idx}] must be an object.")
                continue

            action_type = str(action.get("type") or "")
            if not action_type:
                errors.append(f"phase '{phase_label}' action[{idx}] requires field 'type'.")
                continue

            if action_type == "wait":
                params = action.get("parameters")
                seconds = params.get("seconds") if isinstance(params, dict) else None
                if seconds in (None, ""):
                    errors.append(f"phase '{phase_label}' action[{idx}] missing required field 'seconds'.")
                    continue
                try:
                    seconds = float(seconds)
                except (TypeError, ValueError):
                    errors.append(f"phase '{phase_label}' action[{idx}] field 'seconds' must be numeric.")
                    continue
                if seconds < 0:
                    errors.append(f"phase '{phase_label}' action[{idx}] field 'seconds' must be >= 0.")
                    continue

            if action_type in {"check_connectivity", "cleanup_zenoh_routers", "run_runtime_preset"}:
                if action.get("target") in (None, ""):
                    errors.append(f"phase '{phase_label}' action[{idx}] missing required field 'target'.")
                    continue

            if action_type == "collect_metrics":
                if action.get("target") in (None, ""):
                    errors.append(f"phase '{phase_label}' action[{idx}] collect_metrics requires field 'target'.")
                    continue

            if action_type != "run_runtime_preset":
                continue

            preset_name = str(action.get("preset") or "").strip()
            if not preset_name:
                errors.append(f"phase '{phase_label}' action[{idx}] missing required field 'preset'.")
                continue
            if preset_name in preset_names:
                continue
            if preset_name.startswith("stress/") and is_preset_containers_fallback_enabled():
                fallback_name = preset_name.split("/", 1)[1].strip()
                if fallback_name and resolve_preset_container(fallback_name) is not None:
                    continue
            errors.append(
                f"phase '{phase_label}' action[{idx}] preset '{preset_name}' is not available in runtime environment."
            )

    return {
        "ok": not errors,
        "errors": errors,
        "checked_phases": checked_phase_names,
        "checked_actions": checked_actions,
    }


def _node_vm_candidates(node: dict) -> list[str]:
    candidates: list[str] = []
    ref = node.get("ref")
    node_name = node.get("nodeName") or node.get("name")
    if ref:
        candidates.append(str(ref))
    if node_name:
        candidates.append(str(node_name))
    deduped: list[str] = []
    for item in candidates:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _resolve_vm_for_node(user_id: int, node: dict) -> VM | None:
    for candidate in _node_vm_candidates(node):
        vm = VM.objects.select_related("credential").filter(created_by_id=user_id, name=candidate).first()
        if vm:
            return vm
    return None


def _vm_to_inventory_payload(vm: VM) -> dict[str, Any]:
    key_path = vm.credential.key_file.path if vm.credential and vm.credential.key_file else (vm.credential.key_path if vm.credential else "")
    return {
        "name": vm.name,
        "address": vm.address,
        "user_name": vm.user_name,
        "port": vm.port,
        "status": vm.status,
        "metadata": vm.metadata if isinstance(vm.metadata, dict) else {},
        "networking": vm.networking if isinstance(vm.networking, dict) else {},
        "managed_containers": (
            [str(item).strip() for item in vm.managed_containers if str(item).strip()]
            if isinstance(vm.managed_containers, list)
            else []
        ),
        "credential": {"key_path": key_path},
    }


def _normalize_container_names(raw_value: Any) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    names: list[str] = []
    for item in raw_value:
        name = str(item).strip()
        if not name or name in names:
            continue
        names.append(name)
    return names


def _collect_managed_container_status(
    ssh_client: VMSSHClient,
    tracked_containers: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    running_items = ssh_client.list_running_containers(timeout=15)
    running_by_name: dict[str, dict[str, str]] = {}
    for item in running_items:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("name") or "").strip()
        if item_name:
            running_by_name[item_name] = item

    statuses: list[dict[str, str]] = []
    running_names: list[str] = []
    for name in tracked_containers:
        running_info = running_by_name.get(name)
        if isinstance(running_info, dict):
            statuses.append(
                {
                    "name": name,
                    "state": "running",
                    "status": str(running_info.get("status") or ""),
                }
            )
            running_names.append(name)
            continue

        container_status = ssh_client.get_container_status(name, timeout=15)
        if container_status.running:
            statuses.append(
                {
                    "name": name,
                    "state": "running",
                    "status": str(container_status.status or ""),
                }
            )
            running_names.append(name)
            continue
        if container_status.exists:
            statuses.append(
                {
                    "name": name,
                    "state": "stopped",
                    "status": str(container_status.status or ""),
                }
            )
            continue
        statuses.append(
            {
                "name": name,
                "state": "removed",
                "status": "",
            }
        )

    return statuses, running_names


def _collect_target_nodes_for_phase(
    spec: dict,
    phase_name: str | None,
    *,
    edge_name: str | None = None,
) -> set[str]:
    selected_targets: set[str] = set()
    phase_templates, _ = _extract_phase_templates(spec, phase_name, edge_name=edge_name)
    for phase in phase_templates:
        actions = phase.get("actions") or []
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            target = action.get("target")
            if target:
                target_text = str(target)
                if "${edge." not in target_text:
                    selected_targets.add(target_text)
            targets = action.get("targets")
            if isinstance(targets, list):
                for item in targets:
                    if not item:
                        continue
                    item_text = str(item)
                    if "${edge." in item_text:
                        continue
                    selected_targets.add(item_text)
    return selected_targets


def _build_inventory_by_node(user_id: int, scenario: Scenario) -> tuple[dict[str, dict], list[str]]:
    inventory_by_node: dict[str, dict] = {}
    errors: list[str] = []
    nodes = scenario.nodes if isinstance(scenario.nodes, list) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_name = str(node.get("nodeName") or node.get("name") or "")
        node_kind = str(node.get("kind") or node.get("type") or "").lower()
        if not node_name or node_kind != "vm":
            continue
        vm = _resolve_vm_for_node(user_id, node)
        if vm is None:
            errors.append(f"Inventory VM not found for node '{node_name}'.")
            continue
        if vm.credential is None:
            errors.append(f"VM '{vm.name}' for node '{node_name}' has no SSH credential.")
            continue
        inventory_by_node[node_name] = _vm_to_inventory_payload(vm)
    return inventory_by_node, errors


def _resolve_runtime_env_payload(user_id: int, scenario: Scenario) -> tuple[dict[str, Any], str | None]:
    runtime_env_items = scenario.runtime_env if isinstance(scenario.runtime_env, list) else []
    runtime_env_name = ""
    for item in runtime_env_items:
        if isinstance(item, str) and item.strip():
            runtime_env_name = item.strip()
            break
        if isinstance(item, dict):
            candidate = str(item.get("name") or "").strip()
            if candidate:
                runtime_env_name = candidate
                break
    if not runtime_env_name:
        return {}, "Scenario runtime_env is empty; runtime presets cannot be executed."

    runtime_env = RuntimeEnvironment.objects.filter(
        created_by_id=user_id,
        name=runtime_env_name,
    ).first()
    if runtime_env is None:
        return {}, f"Runtime environment '{runtime_env_name}' not found."

    return (
        {
            "name": runtime_env.name,
            "images": runtime_env.images if isinstance(runtime_env.images, dict) else {},
            "tags": runtime_env.tags if isinstance(runtime_env.tags, dict) else {},
            "parameters": runtime_env.parameters if isinstance(runtime_env.parameters, dict) else {},
            "command_preset": (
                runtime_env.command_preset if isinstance(runtime_env.command_preset, list) else []
            ),
            "commandPresets": (
                runtime_env.command_preset if isinstance(runtime_env.command_preset, list) else []
            ),
            "command_groups": (
                runtime_env.command_groups if isinstance(runtime_env.command_groups, dict) else {}
            ),
            "serializer_rrt_config_raw_yaml": str(runtime_env.serializer_rrt_config_raw_yaml or ""),
            "serializer_rrt_config_path": str(runtime_env.serializer_rrt_config_path or ""),
            "uploaded_version": int(runtime_env.uploaded_version or 0),
        },
        None,
    )


def _as_positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError(f"Field '{field_name}' must be a positive integer.") from exc
    if parsed <= 0:
        raise ActionExecutionError(f"Field '{field_name}' must be a positive integer.")
    return parsed


def _parse_first_int_line(value: str, field_name: str) -> int:
    line = (value or "").strip().splitlines()
    if not line:
        raise ActionExecutionError(f"Failed to parse '{field_name}' from empty SSH output.")
    return _as_positive_int(line[0].strip(), field_name)


def _build_validate_folder_name() -> str:
    return f"validate-{timezone.now().strftime('%Y%m%d-%H%M%S-%f')}"


def _build_validate_remote_dir(folder_name: str) -> str:
    return f"{_PROBE_METRICS_REMOTE_BASE_DIR}/{folder_name}"


def _build_validate_local_dir(folder_name: str) -> Path:
    return Path(settings.STATIC_ROOT, *_PROBE_METRICS_STATIC_SUBDIR, folder_name)


def _is_stress_preset_action(action: dict[str, Any]) -> bool:
    return (
        str(action.get("type") or "") == "run_runtime_preset"
        and str(action.get("preset") or "").startswith("stress/")
    )


def _is_profiling_preset_action(action: dict[str, Any]) -> bool:
    return (
        str(action.get("type") or "") == "run_runtime_preset"
        and str(action.get("preset") or "").startswith("profiling/")
    )


def _action_flag(parameters: Any, key: str) -> bool:
    if not isinstance(parameters, dict):
        return False
    value = parameters.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_stress_cleanup_action(action: dict[str, Any]) -> bool:
    return _is_stress_preset_action(action) and _action_flag(action.get("parameters"), "cleanup_only")


def _build_vm_ssh_client(vm_payload: dict[str, Any], action: dict[str, Any]) -> VMSSHClient:
    try:
        return build_vm_ssh_client(
            vm_payload,
            action=action,
            default_connect_timeout=8,
        )
    except ValueError as exc:
        raise ActionExecutionError(str(exc)) from exc


def _resolve_scp_collect_targets(
    inventory_by_node: dict[str, dict[str, Any]],
    target_nodes: set[str],
) -> tuple[list[SCPCollectTarget], list[str]]:
    targets: list[SCPCollectTarget] = []
    errors: list[str] = []
    for node_name in sorted(target_nodes):
        vm_payload = inventory_by_node.get(node_name)
        if not isinstance(vm_payload, dict):
            errors.append(f"Probe metrics collection target '{node_name}' not found in inventory.")
            continue
        host = str(vm_payload.get("address") or "").strip()
        username = str(vm_payload.get("user_name") or vm_payload.get("username") or "").strip()
        if not host or not username:
            errors.append(
                f"Probe metrics collection target '{node_name}' is missing SSH host/user."
            )
            continue
        key_path = (
            vm_payload.get("key_path")
            or vm_payload.get("ssh_key_path")
            or (vm_payload.get("credential") or {}).get("key_path")
            or vm_payload.get("credential_key_path")
            or (vm_payload.get("credential") or {}).get("key_file")
        )
        if not key_path:
            errors.append(
                f"Probe metrics collection target '{node_name}' does not include an SSH key path."
            )
            continue
        targets.append(
            SCPCollectTarget(
                node_name=node_name,
                host=host,
                username=username,
                port=int(vm_payload.get("port") or 22),
                key_path=str(key_path),
            )
        )
    return targets, errors


def _collect_probe_metrics(
    *,
    inventory_by_node: dict[str, dict[str, Any]],
    target_nodes: set[str],
    folder_name: str,
    remote_dir: str | None = None,
    local_dir: str | None = None,
    connect_timeout_sec: int = 8,
    timeout_sec: int = 45,
) -> dict[str, Any]:
    resolved_remote_dir = remote_dir or _build_validate_remote_dir(folder_name)
    resolved_local_dir = Path(local_dir) if local_dir else _build_validate_local_dir(folder_name)
    collect_targets, resolve_errors = _resolve_scp_collect_targets(
        inventory_by_node=inventory_by_node,
        target_nodes=target_nodes,
    )
    if not collect_targets:
        return {
            "ok": False,
            "folder_name": folder_name,
            "remote_dir": resolved_remote_dir,
            "local_dir": str(resolved_local_dir),
            "targets": {},
            "errors": resolve_errors or ["No valid targets for probe metrics collection."],
        }

    scp_result = VMScpCollectMetricsExec(
        targets=collect_targets,
        remote_dir=resolved_remote_dir,
        local_base_dir=str(resolved_local_dir),
        connect_timeout_sec=connect_timeout_sec,
        timeout_sec=timeout_sec,
    ).execute()
    errors = list(resolve_errors)
    errors.extend(str(item) for item in scp_result.get("errors") or [])
    return {
        "ok": bool(scp_result.get("ok")) and not errors,
        "folder_name": folder_name,
        "remote_dir": resolved_remote_dir,
        "local_dir": str(resolved_local_dir),
        "target_nodes": sorted(target_nodes),
        "targets": scp_result.get("targets") or {},
        "errors": errors,
    }


def _find_pending_metrics_source_run(
    *,
    user_id: int,
    scenario: Scenario,
    exclude_run_id: int | None = None,
) -> ScenarioValidationRun | None:
    queryset = ScenarioValidationRun.objects.filter(
        scenario=scenario,
        requested_by_id=user_id,
        need_to_collect_metrics=True,
    )
    if exclude_run_id is not None:
        queryset = queryset.exclude(id=exclude_run_id)
    return queryset.order_by("-requested_at").first()


def _upsert_run_vm_metrics(
    *,
    run: ScenarioValidationRun,
    user_id: int,
    phase_name: str,
    probe_metrics: dict[str, Any],
) -> None:
    if not isinstance(probe_metrics, dict):
        return
    targets_payload = probe_metrics.get("targets")
    if not isinstance(targets_payload, dict) or not targets_payload:
        return
    remote_dir = str(probe_metrics.get("remote_dir") or "")
    local_dir = str(probe_metrics.get("local_dir") or "")
    for node_name, metrics_payload in targets_payload.items():
        node_text = str(node_name or "").strip()
        if not node_text:
            continue
        vm_obj = VM.objects.filter(created_by_id=user_id, name=node_text).first()
        defaults = {
            "vm": vm_obj,
            "phase_name": str(phase_name or ""),
            "remote_dir": remote_dir,
            "local_dir": local_dir,
            "metrics": metrics_payload if isinstance(metrics_payload, dict) else {"value": metrics_payload},
            "collected": bool((metrics_payload or {}).get("ok")) if isinstance(metrics_payload, dict) else True,
        }
        ScenarioValidationRunVM.objects.update_or_create(
            run=run,
            node_name=node_text,
            defaults=defaults,
        )


def _trim_history_text(value: Any) -> str:
    text = str(value or "")
    if len(text) <= _MAX_HISTORY_TEXT_LEN:
        return text
    return f"{text[:_MAX_HISTORY_TEXT_LEN]}...(truncated)"


def _record_ssh_access_history(
    *,
    user_id: int | None,
    vm_name: str,
    scenario_name: str,
    phase_name: str,
    action_type: str,
    command: str,
    success: bool,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    source: str = "celery_task",
) -> None:
    vm = VM.objects.filter(created_by_id=user_id, name=vm_name).first() if user_id is not None else None
    SSHAccessHistory.objects.create(
        vm=vm,
        requested_by_id=user_id if user_id is not None else None,
        source=source,
        scenario_name=scenario_name,
        phase_name=phase_name,
        action_type=action_type,
        command=command,
        success=success,
        returncode=returncode,
        stdout=_trim_history_text(stdout),
        stderr=_trim_history_text(stderr),
    )


def _run_ssh_command_with_history(
    ssh_client: VMSSHClient,
    *,
    command: str,
    timeout: int,
    user_id: int | None,
    vm_name: str,
    scenario_name: str,
    phase_name: str,
    action_type: str,
    source: str = "celery_task",
):
    result = ssh_client.execute_command(command, timeout=timeout)
    _record_ssh_access_history(
        user_id=user_id,
        vm_name=vm_name,
        scenario_name=scenario_name,
        phase_name=phase_name,
        action_type=action_type,
        command=command,
        success=bool(result.success),
        returncode=int(result.returncode),
        stdout=result.stdout,
        stderr=result.stderr,
        source=source,
    )
    return result


def _discover_vm_capabilities(
    vm_payload: dict[str, Any],
    action: dict[str, Any],
    *,
    user_id: int,
    scenario_name: str,
    phase_name: str,
    action_type: str,
) -> dict[str, Any]:
    ssh_client = _build_vm_ssh_client(vm_payload, action)
    vm_name = str(vm_payload.get("name") or "")
    cpu_result = _run_ssh_command_with_history(
        ssh_client,
        command="nproc",
        timeout=15,
        user_id=user_id,
        vm_name=vm_name,
        scenario_name=scenario_name,
        phase_name=phase_name,
        action_type=action_type,
        source="celery_task:stress_capability_probe",
    )
    if not cpu_result.success:
        raise ActionExecutionError(
            f"Failed to collect CPU cores via SSH: {cpu_result.stderr or 'unknown error'}"
        )
    cpu_cores = _parse_first_int_line(cpu_result.stdout, "cpu_cores")

    mem_result = _run_ssh_command_with_history(
        ssh_client,
        command="awk '/MemTotal:/ {print $2}' /proc/meminfo",
        timeout=15,
        user_id=user_id,
        vm_name=vm_name,
        scenario_name=scenario_name,
        phase_name=phase_name,
        action_type=action_type,
        source="celery_task:stress_capability_probe",
    )
    if not mem_result.success:
        raise ActionExecutionError(
            f"Failed to collect total RAM via SSH: {mem_result.stderr or 'unknown error'}"
        )
    mem_total_kb = _parse_first_int_line(mem_result.stdout, "mem_total_kb")
    mem_total_gb = mem_total_kb / (1024 * 1024)

    nvidia_result = _run_ssh_command_with_history(
        ssh_client,
        command="nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || true",
        timeout=15,
        user_id=user_id,
        vm_name=vm_name,
        scenario_name=scenario_name,
        phase_name=phase_name,
        action_type=action_type,
        source="celery_task:stress_capability_probe",
    )
    gpu_lines = [line.strip() for line in (nvidia_result.stdout or "").splitlines() if line.strip()]
    if not gpu_lines:
        lspci_result = _run_ssh_command_with_history(
            ssh_client,
            command="lspci | grep -Ei 'vga|3d|nvidia|amd' || true",
            timeout=15,
            user_id=user_id,
            vm_name=vm_name,
            scenario_name=scenario_name,
            phase_name=phase_name,
            action_type=action_type,
            source="celery_task:stress_capability_probe",
        )
        gpu_lines = [line.strip() for line in (lspci_result.stdout or "").splitlines() if line.strip()]

    return {
        "cpu_cores": cpu_cores,
        "mem_total_gb": round(mem_total_gb, 3),
        "gpu": gpu_lines,
        "collected_at": timezone.now().isoformat(),
        "collector_version": _CAPABILITIES_COLLECTOR_VERSION,
    }


def _cache_vm_capabilities(user_id: int, vm_payload: dict[str, Any], capabilities: dict[str, Any]) -> None:
    vm_name = str(vm_payload.get("name") or "")
    if not vm_name:
        return
    vm = VM.objects.filter(created_by_id=user_id, name=vm_name).first()
    if vm is None:
        return
    metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
    metadata["capabilities"] = capabilities
    vm.metadata = metadata
    vm.system_info = capabilities
    vm.modified_by_id = user_id
    vm.save(update_fields=["metadata", "system_info", "modified_by", "updated_at"])


def _validate_stress_parameters_against_capabilities(action: dict[str, Any], capabilities: dict[str, Any]) -> None:
    parameters = action.get("parameters")
    if parameters is None:
        return
    if not isinstance(parameters, dict):
        raise ActionExecutionError("Action field 'parameters' must be an object when provided.")

    if parameters.get("cpu_cores") not in (None, ""):
        requested_cpu = _as_positive_int(parameters.get("cpu_cores"), "parameters.cpu_cores")
        detected_cpu_cores = _as_positive_int(capabilities.get("cpu_cores"), "capabilities.cpu_cores")
        cpu_limit = max(1, math.floor(detected_cpu_cores * 0.8))
        if requested_cpu > cpu_limit:
            raise ActionExecutionError(
                f"Requested cpu_cores={requested_cpu} exceeds cpu safety limit {cpu_limit} "
                f"(80% of detected {detected_cpu_cores} cores)."
            )

    if parameters.get("mem_gb") not in (None, ""):
        requested_mem_gb = _as_positive_int(parameters.get("mem_gb"), "parameters.mem_gb")
        try:
            mem_total_gb = float(capabilities.get("mem_total_gb"))
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("Detected VM capability 'mem_total_gb' is invalid.") from exc
        mem_limit_gb = max(1, math.floor(mem_total_gb * 0.8))
        if requested_mem_gb > mem_limit_gb:
            raise ActionExecutionError(
                f"Requested mem_gb={requested_mem_gb} exceeds safety limit {mem_limit_gb} GiB "
                f"(80% of detected {mem_total_gb:.2f} GiB)."
            )


def _extract_container_names_from_debug(debug: dict[str, Any]) -> tuple[set[str], set[str]]:
    created: set[str] = set()
    removed: set[str] = set()
    if not isinstance(debug, dict):
        return created, removed
    texts = []
    for key in ("command", "command_final", "command_rendered"):
        value = debug.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    for text in texts:
        for match in re.finditer(r"docker\s+run\b[^\n;|&]*?--name\s+([A-Za-z0-9_.-]+)", text):
            created.add(match.group(1))
        for match in re.finditer(r"docker\s+rm\s+-f\s+([^\n;|&]+)", text):
            token_block = match.group(1)
            for token in re.split(r"\s+", token_block.strip()):
                cleaned = token.strip()
                if not cleaned:
                    continue
                if cleaned.startswith((">", "<", "2>", "1>", "||", "&&")):
                    break
                cleaned = cleaned.strip(";")
                if re.match(r"^[A-Za-z0-9_.-]+$", cleaned):
                    removed.add(cleaned)
    return created, removed


def _extract_named_container_set(debug: dict[str, Any], key: str) -> set[str]:
    value = debug.get(key) if isinstance(debug, dict) else None
    if not isinstance(value, list):
        return set()
    names: set[str] = set()
    for item in value:
        name = str(item).strip()
        if name:
            names.add(name)
    return names


def _update_vm_managed_containers(
    *,
    user_id: int,
    vm_name: str,
    created: set[str],
    removed: set[str],
) -> None:
    if not vm_name or (not created and not removed):
        return
    vm = VM.objects.filter(created_by_id=user_id, name=vm_name).first()
    if vm is None:
        return
    current_raw = vm.managed_containers if isinstance(vm.managed_containers, list) else []
    current = {str(item) for item in current_raw if str(item).strip()}
    current.update(created)
    current.difference_update(removed)
    vm.managed_containers = sorted(current)
    vm.modified_by_id = user_id
    vm.save(update_fields=["managed_containers", "modified_by", "updated_at"])


def _action_debug_snapshot(action: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "type": str(action.get("type") or ""),
        "target": str(action.get("target") or ""),
        "preset": str(action.get("preset") or ""),
    }
    targets = action.get("targets")
    if isinstance(targets, list):
        snapshot["targets"] = [str(item) for item in targets[:_PROGRESS_MAX_LIST_ITEMS]]
        if len(targets) > _PROGRESS_MAX_LIST_ITEMS:
            snapshot["targets_truncated"] = True
    parameters = action.get("parameters")
    if isinstance(parameters, dict):
        snapshot["parameter_keys"] = sorted(str(key) for key in parameters.keys())
    return snapshot


def _execute_phase_actions(
    user_id: int,
    scenario: Scenario,
    *,
    spec: dict,
    phase_name: str,
    edge_name: str | None = None,
    probe_metrics_folder_name: str | None = None,
    probe_metrics_remote_dir: str | None = None,
    probe_metrics_local_dir: str | None = None,
    current_run_id: int | None = None,
    emit=None,
) -> dict[str, Any]:
    phase_templates, phase_errors = _extract_phase_templates(spec, phase_name, edge_name=edge_name)
    if phase_errors:
        return {
            "ok": False,
            "phase_name": phase_name,
            "executed_actions": 0,
            "results": [],
            "errors": phase_errors,
        }
    phase = phase_templates[0] if phase_templates else {}
    actions = phase.get("actions") if isinstance(phase, dict) else []
    if not isinstance(actions, list) or not actions:
        return {
            "ok": False,
            "phase_name": phase_name,
            "executed_actions": 0,
            "results": [],
            "errors": [f"Phase '{phase_name}' has no actions to execute."],
        }

    inventory_by_node, inventory_errors = _build_inventory_by_node(user_id, scenario)
    if inventory_errors:
        return {
            "ok": False,
            "phase_name": phase_name,
            "executed_actions": 0,
            "results": [],
            "errors": inventory_errors,
        }

    runtime_env_payload, runtime_error = _resolve_runtime_env_payload(user_id, scenario)
    if runtime_error:
        return {
            "ok": False,
            "phase_name": phase_name,
            "executed_actions": 0,
            "results": [],
            "errors": [runtime_error],
        }

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    executed_actions = 0
    scenario_name = str(scenario.name or "")
    validate_folder_name = probe_metrics_folder_name or _build_validate_folder_name()
    validate_remote_dir = probe_metrics_remote_dir or _build_validate_remote_dir(validate_folder_name)
    validate_local_dir = probe_metrics_local_dir or str(_build_validate_local_dir(validate_folder_name))
    profiling_target_nodes: set[str] = set()
    probe_metrics: dict[str, Any] = {}
    if emit:
        emit(
            "phase_execution_context",
            phase_name=phase_name,
            edge_name=edge_name or "",
            actions_count=len(actions),
            runtime_env_name=runtime_env_payload.get("name"),
            runtime_preset_count=len(runtime_env_payload.get("command_preset") or []),
            inventory_nodes=sorted(inventory_by_node.keys()),
        )
    if phase_name == _PROBE_PHASE_NAME and emit:
        emit(
            "probe_metrics_folder_created",
            phase_name=phase_name,
            folder_name=validate_folder_name,
            remote_dir=validate_remote_dir,
        )
    for index, raw_action in enumerate(actions):
        if not isinstance(raw_action, dict):
            errors.append(f"Phase '{phase_name}' action[{index}] must be an object.")
            break
        action = dict(raw_action)
        action_type = str(action.get("type") or "")
        target = str(action.get("target") or "")
        action_snapshot = _action_debug_snapshot(action)
        if emit:
            emit(
                "phase_action_plan",
                phase_name=phase_name,
                index=index,
                action_type=action_type,
                target=target,
                preset=action_snapshot.get("preset"),
                parameter_keys=action_snapshot.get("parameter_keys"),
                action_snapshot=action_snapshot,
            )

        if action_type == "collect_metrics":
            requested_targets: set[str] = set()
            single_target = str(action.get("target") or "").strip()
            if single_target:
                requested_targets.add(single_target)
            requested_targets_raw = action.get("targets")
            if isinstance(requested_targets_raw, list):
                requested_targets.update(str(item).strip() for item in requested_targets_raw if str(item).strip())
            collect_params = action.get("parameters")
            if not isinstance(collect_params, dict):
                collect_params = {}
            try:
                collect_connect_timeout_sec = _as_positive_int(
                    collect_params.get("connect_timeout_sec", 8),
                    "connect_timeout_sec",
                )
                collect_timeout_sec = _as_positive_int(
                    collect_params.get("timeout_sec", 45),
                    "timeout_sec",
                )
            except ActionExecutionError as exc:
                errors.append(f"Phase '{phase_name}' action[{index}] failed: {exc}")
                if emit:
                    emit(
                        "phase_action_failed",
                        phase_name=phase_name,
                        index=index,
                        action_type=action_type,
                        target=target,
                        error=str(exc),
                        action_snapshot=action_snapshot,
                    )
                break
            source_run = _find_pending_metrics_source_run(
                user_id=user_id,
                scenario=scenario,
                exclude_run_id=current_run_id,
            )
            if source_run is None:
                previous_source_run_id = probe_metrics.get("source_run_id")
                if previous_source_run_id not in (None, ""):
                    source_run = ScenarioValidationRun.objects.filter(
                        id=previous_source_run_id,
                        scenario=scenario,
                    ).first()
            if source_run is None:
                errors.append(
                    f"Phase '{phase_name}' action[{index}] failed: no pending probe metrics found to collect."
                )
                break

            source_probe_metrics = source_run.probe_metrics if isinstance(source_run.probe_metrics, dict) else {}
            source_folder_name = str(source_probe_metrics.get("folder_name") or "").strip()
            source_remote_dir = str(source_run.metrics_remote_dir or source_probe_metrics.get("remote_dir") or "").strip()
            source_local_dir = str(source_run.metrics_local_dir or source_probe_metrics.get("local_dir") or "").strip()
            if not source_folder_name:
                source_folder_name = (
                    source_remote_dir.rstrip("/").split("/")[-1]
                    if source_remote_dir.strip()
                    else validate_folder_name
                )
            if not source_remote_dir:
                source_remote_dir = _build_validate_remote_dir(source_folder_name)
            if not source_local_dir:
                source_local_dir = str(_build_validate_local_dir(source_folder_name))
            if not requested_targets:
                source_targets = source_probe_metrics.get("target_nodes")
                if isinstance(source_targets, list):
                    requested_targets = {str(item) for item in source_targets if str(item).strip()}
            if not requested_targets:
                errors.append(
                    f"Phase '{phase_name}' action[{index}] failed: no targets available for metrics collection."
                )
                break

            if emit:
                emit(
                    "probe_metrics_collect_start",
                    phase_name=phase_name,
                    folder_name=source_folder_name,
                    remote_dir=source_remote_dir,
                    local_dir=source_local_dir,
                    target_count=len(requested_targets),
                    connect_timeout_sec=collect_connect_timeout_sec,
                    timeout_sec=collect_timeout_sec,
                )
            collected_probe_metrics = _collect_probe_metrics(
                inventory_by_node=inventory_by_node,
                target_nodes=requested_targets,
                folder_name=source_folder_name,
                remote_dir=source_remote_dir,
                local_dir=source_local_dir,
                connect_timeout_sec=collect_connect_timeout_sec,
                timeout_sec=collect_timeout_sec,
            )
            probe_metrics = collected_probe_metrics
            probe_metrics["source_run_id"] = source_run.id
            if emit:
                emit(
                    "probe_metrics_collect_done",
                    phase_name=phase_name,
                    folder_name=source_folder_name,
                    ok=collected_probe_metrics.get("ok"),
                    local_dir=collected_probe_metrics.get("local_dir"),
                )
            if collected_probe_metrics.get("ok"):
                source_run.need_to_collect_metrics = False
                source_run.metrics_remote_dir = str(collected_probe_metrics.get("remote_dir") or source_remote_dir)
                source_run.metrics_local_dir = str(collected_probe_metrics.get("local_dir") or source_local_dir)
                source_run.probe_metrics = {
                    **source_probe_metrics,
                    **collected_probe_metrics,
                    "collected": True,
                    "needs_collection": False,
                    "collected_by_run_id": current_run_id,
                    "collected_at": timezone.now().isoformat(),
                }
                source_run.save(
                    update_fields=[
                        "need_to_collect_metrics",
                        "metrics_remote_dir",
                        "metrics_local_dir",
                        "probe_metrics",
                    ]
                )
                _upsert_run_vm_metrics(
                    run=source_run,
                    user_id=user_id,
                    phase_name=source_run.phase_name or _PROBE_PHASE_NAME,
                    probe_metrics=source_run.probe_metrics,
                )
            else:
                for err in collected_probe_metrics.get("errors") or []:
                    errors.append(str(err))

            action_result = {
                "index": index,
                "type": action_type,
                "status": "SUCCESS" if bool(collected_probe_metrics.get("ok")) else "FAILED",
                "success": bool(collected_probe_metrics.get("ok")),
                "message": (
                    f"Collected pending probe metrics from run#{source_run.id}."
                    if bool(collected_probe_metrics.get("ok"))
                    else f"Failed to collect pending probe metrics from run#{source_run.id}."
                ),
                "debug": {
                    "source_run_id": source_run.id,
                    "requested_targets": sorted(requested_targets),
                    "connect_timeout_sec": collect_connect_timeout_sec,
                    "timeout_sec": collect_timeout_sec,
                    "probe_metrics": collected_probe_metrics,
                },
            }
            results.append(action_result)
            executed_actions += 1
            if emit:
                emit(
                    "phase_action_done",
                    phase_name=phase_name,
                    index=index,
                    action_type=action_type,
                    status=action_result["status"],
                    success=action_result["success"],
                )
            if not action_result["success"]:
                errors.append(
                    f"Phase '{phase_name}' action[{index}] returned status '{action_result['status']}'."
                )
                break
            continue

        if _is_profiling_preset_action(action):
            if target:
                profiling_target_nodes.add(target)
            action_parameters = action.get("parameters")
            if not isinstance(action_parameters, dict):
                action_parameters = {}
            action_parameters.setdefault("icopa_metrics_dir", validate_remote_dir)
            action_parameters.setdefault("validate_metrics_dir", validate_remote_dir)
            action_parameters.setdefault("validate_metrics_path", validate_remote_dir)
            action["parameters"] = action_parameters

        if _is_stress_preset_action(action) and not _is_stress_cleanup_action(action):
            vm_payload = inventory_by_node.get(target)
            if vm_payload is None:
                errors.append(
                    f"Phase '{phase_name}' action[{index}] target '{target}' not found in inventory_by_node."
                )
                break
            try:
                capabilities = _discover_vm_capabilities(
                    vm_payload,
                    action,
                    user_id=user_id,
                    scenario_name=scenario_name,
                    phase_name=phase_name,
                    action_type=action_type,
                )
                _cache_vm_capabilities(user_id, vm_payload, capabilities)
                _validate_stress_parameters_against_capabilities(action, capabilities)
                action["__vm_capabilities"] = capabilities
                vm_payload_metadata = vm_payload.get("metadata")
                if not isinstance(vm_payload_metadata, dict):
                    vm_payload_metadata = {}
                    vm_payload["metadata"] = vm_payload_metadata
                vm_payload_metadata["capabilities"] = capabilities
                if emit:
                    emit(
                        "stress_capabilities_collected",
                        phase_name=phase_name,
                        index=index,
                        target=target,
                        cpu_cores=capabilities.get("cpu_cores"),
                        mem_total_gb=capabilities.get("mem_total_gb"),
                        gpu_count=len(capabilities.get("gpu") or []),
                    )
            except ActionExecutionError as exc:
                errors.append(f"Phase '{phase_name}' action[{index}] failed: {exc}")
                if emit:
                    emit(
                        "phase_action_failed",
                        phase_name=phase_name,
                        index=index,
                        action_type=action_type,
                        target=target,
                        error=str(exc),
                        action_snapshot=action_snapshot,
                    )
                break

        if emit:
            emit(
                "phase_action_start",
                phase_name=phase_name,
                index=index,
                action_type=action_type,
                target=target,
            )
        if action_type == "wait":
            params = action.get("parameters")
            raw_seconds = params.get("seconds", 0) if isinstance(params, dict) else 0
            try:
                seconds = float(raw_seconds)
            except (TypeError, ValueError):
                errors.append(f"Phase '{phase_name}' action[{index}] wait.seconds must be numeric.")
                break
            if seconds < 0:
                errors.append(f"Phase '{phase_name}' action[{index}] wait.seconds must be >= 0.")
                break
            time.sleep(seconds)
            executed_actions += 1
            wait_result = {
                "index": index,
                "type": "wait",
                "seconds": seconds,
                "status": "SUCCESS",
                "success": True,
                "message": f"Waited {seconds:g}s.",
            }
            results.append(wait_result)
            if emit:
                emit("phase_action_done", phase_name=phase_name, **wait_result)
            continue

        try:
            ctx = ExecutionContext(
                inventory_by_node=inventory_by_node,
                runtime_env=runtime_env_payload,
                scenario=scenario.raw_payload if isinstance(scenario.raw_payload, dict) else {},
                phase={"name": phase_name},
                action=action,
            )
            executor = build_executor(ctx)
            if emit:
                emit(
                    "phase_action_executor_resolved",
                    phase_name=phase_name,
                    index=index,
                    action_type=action_type,
                    target=target,
                    executor=executor.__class__.__name__,
                )
            outcome = executor.execute()
        except ActionExecutionError as exc:
            errors.append(f"Phase '{phase_name}' action[{index}] failed: {exc}")
            if emit:
                emit(
                    "phase_action_failed",
                    phase_name=phase_name,
                    index=index,
                    action_type=action_type,
                    target=target,
                    error=str(exc),
                    action_snapshot=action_snapshot,
                )
            break

        debug_payload = outcome.debug if isinstance(outcome.debug, dict) else {}
        if emit:
            emit(
                "phase_action_execution_result",
                phase_name=phase_name,
                index=index,
                action_type=action_type,
                target=target,
                status=outcome.status,
                success=outcome.success,
                message=outcome.message,
                log_count=len(outcome.logs) if isinstance(outcome.logs, list) else 0,
                debug_keys=sorted(str(key) for key in debug_payload.keys()),
            )
        ssh_command = ""
        for command_key in ("command", "command_final", "command_rendered"):
            candidate = debug_payload.get(command_key)
            if isinstance(candidate, str) and candidate.strip():
                ssh_command = candidate
                break
        vm_payload = inventory_by_node.get(target) if target else None
        vm_name = str((vm_payload or {}).get("name") or target or "")
        if ssh_command and vm_name:
            _record_ssh_access_history(
                user_id=user_id,
                vm_name=vm_name,
                scenario_name=scenario_name,
                phase_name=phase_name,
                action_type=action_type,
                command=ssh_command,
                success=bool(outcome.success),
                returncode=int(debug_payload.get("returncode") or (0 if outcome.success else 1)),
                stdout="\n".join(outcome.logs) if isinstance(outcome.logs, list) else "",
                stderr=str(debug_payload.get("stderr") or ""),
                source="celery_task:action_outcome",
            )
        if outcome.success:
            created_names, removed_names = _extract_container_names_from_debug(debug_payload)
            created_names.update(_extract_named_container_set(debug_payload, "managed_containers_created"))
            created_names.update(_extract_named_container_set(debug_payload, "managed_containers_running"))
            removed_names.update(_extract_named_container_set(debug_payload, "managed_containers_removed"))
            # Stress startup can emit "docker rm -f <name>; docker run --name <name> ..."
            # in one command. Treat that as created/running, not removed.
            removed_names.difference_update(created_names)
            _update_vm_managed_containers(
                user_id=user_id,
                vm_name=vm_name,
                created=created_names,
                removed=removed_names,
            )

        action_result = {
            "index": index,
            "type": action_type,
            "target": target,
            "status": outcome.status,
            "success": outcome.success,
            "message": outcome.message,
            "debug": outcome.debug,
        }
        results.append(action_result)
        executed_actions += 1
        if emit:
            emit(
                "phase_action_done",
                phase_name=phase_name,
                index=index,
                action_type=action_type,
                target=target,
                status=outcome.status,
                success=outcome.success,
            )
        if not outcome.success:
            errors.append(
                f"Phase '{phase_name}' action[{index}] returned status '{outcome.status}'."
            )
            break

    if phase_name == _PROBE_PHASE_NAME:
        probe_metrics = {
            "folder_name": validate_folder_name,
            "remote_dir": validate_remote_dir,
            "local_dir": validate_local_dir,
            "target_nodes": sorted(profiling_target_nodes),
            "needs_collection": True,
            "collected": False,
            "targets": {},
            "errors": [],
        }
        if emit:
            emit(
                "probe_metrics_pending_collection",
                phase_name=phase_name,
                folder_name=validate_folder_name,
                remote_dir=validate_remote_dir,
                local_dir=validate_local_dir,
                target_count=len(profiling_target_nodes),
            )

    return {
        "ok": not errors,
        "phase_name": phase_name,
        "executed_actions": executed_actions,
        "results": results,
        "errors": errors,
        "probe_metrics": probe_metrics,
    }


def _check_nodes_connectivity(
    inventory_by_node: dict[str, dict],
    required_targets: set[str],
    phase_name: str | None,
    emit=None,
    *,
    user_id: int | None = None,
    scenario_name: str = "",
) -> dict:
    results = []
    ok = True
    for node_name, vm in inventory_by_node.items():
        if required_targets and node_name not in required_targets:
            continue
        if emit:
            emit(
                "node_checking",
                node=node_name,
                host=vm.get("address", ""),
                user=vm.get("user_name", ""),
                port=vm.get("port", 22),
            )
        try:
            ctx = ExecutionContext(
                inventory_by_node={node_name: vm},
                action={"type": "check_connectivity", "target": node_name},
            )
            outcome = VMConnectCheckExec(ctx).execute()
            tracked_containers = _normalize_container_names(vm.get("managed_containers"))
            managed_container_status: list[dict[str, str]] = []
            managed_containers_running: list[str] = []
            managed_containers_ok = True
            managed_container_check_skipped = not tracked_containers
            managed_container_error = ""

            if outcome.success and tracked_containers:
                try:
                    ssh_client = build_vm_ssh_client(
                        vm,
                        action={"type": "check_connectivity"},
                        workdir=str(settings.BASE_DIR),
                    )
                    managed_container_status, managed_containers_running = _collect_managed_container_status(
                        ssh_client,
                        tracked_containers,
                    )
                    managed_containers_ok = all(
                        str(item.get("state") or "") == "running"
                        for item in managed_container_status
                    )
                except Exception as exc:
                    managed_container_error = str(exc)
                    managed_containers_ok = False
                    managed_container_status = [
                        {"name": name, "state": "unknown", "status": ""}
                        for name in tracked_containers
                    ]
                    managed_containers_running = []

            node_ok = bool(outcome.success and managed_containers_ok)
            node_status = outcome.status if node_ok else "FAILED"
            node_message = str(outcome.message or "")
            if outcome.success and not managed_containers_ok:
                if managed_container_error:
                    node_message = (
                        f"{node_message} Managed container check failed: {managed_container_error}"
                    ).strip()
                else:
                    failed_items = [
                        f"{item.get('name')}:{item.get('state')}"
                        for item in managed_container_status
                        if str(item.get("state") or "") != "running"
                    ]
                    failed_summary = ", ".join(failed_items) if failed_items else "unknown"
                    node_message = (
                        f"{node_message} Managed containers not running: {failed_summary}"
                    ).strip()

            debug_payload = outcome.debug if isinstance(outcome.debug, dict) else {}
            node_debug = {
                **debug_payload,
                "managed_containers": tracked_containers,
                "managed_container_status": managed_container_status,
                "managed_containers_running": managed_containers_running,
                "managed_containers_ok": managed_containers_ok,
                "managed_container_check_skipped": managed_container_check_skipped,
            }
            if managed_container_error:
                node_debug["managed_container_error"] = managed_container_error

            if emit:
                emit(
                    "node_checked",
                    node=node_name,
                    ok=node_ok,
                    status=node_status,
                    message=node_message,
                    debug=node_debug,
                    logs=outcome.logs,
                    managed_containers=tracked_containers,
                    managed_containers_ok=managed_containers_ok,
                    managed_container_check_skipped=managed_container_check_skipped,
                )
            results.append(
                {
                    "node": node_name,
                    "ok": node_ok,
                    "status": node_status,
                    "message": node_message,
                    "managed_containers": tracked_containers,
                    "managed_container_status": managed_container_status,
                    "managed_containers_running": managed_containers_running,
                    "managed_containers_ok": managed_containers_ok,
                    "managed_container_check_skipped": managed_container_check_skipped,
                    "debug": node_debug,
                }
            )
            if user_id is not None:
                history_stderr = str(debug_payload.get("stderr") or "")
                if managed_container_error:
                    history_stderr = (
                        f"{history_stderr}\nmanaged_container_error={managed_container_error}".strip()
                    )
                _record_ssh_access_history(
                    user_id=user_id,
                    vm_name=str(vm.get("name") or node_name),
                    scenario_name=scenario_name,
                    phase_name=phase_name or "",
                    action_type="check_connectivity",
                    command=(
                        "test_connectivity(check_container_runtime=true,"
                        f"check_managed_containers={len(tracked_containers)})"
                    ),
                    success=node_ok,
                    returncode=int(debug_payload.get("returncode") or (0 if node_ok else 1)),
                    stdout="\n".join(outcome.logs) if isinstance(outcome.logs, list) else "",
                    stderr=history_stderr,
                    source="celery_task:node_check",
                )
            ok = ok and node_ok
        except ActionExecutionError as exc:
            if emit:
                emit("node_check_failed", node=node_name, error=str(exc))
            ok = False
            results.append(
                {
                    "node": node_name,
                    "ok": False,
                    "status": "FAILED",
                    "message": str(exc),
                    "debug": {},
                }
            )
    return {
        "ok": ok,
        "results": results,
        "phase_name": phase_name,
    }


def _check_graph_ping(
    inventory_by_node: dict[str, dict],
    graph_edges: list[dict[str, Any]],
    emit=None,
    *,
    user_id: int | None = None,
    scenario_name: str = "",
    phase_name: str = "",
    edge_name: str | None = None,
) -> dict:
    if not isinstance(graph_edges, list):
        return {"ok": False, "error": "graph.edges must be a list."}
    selected_edge_name = (edge_name or "").strip()
    selected_edges = graph_edges
    if selected_edge_name:
        selected_edges = [
            edge
            for edge in graph_edges
            if isinstance(edge, dict) and str(edge.get("name") or "").strip() == selected_edge_name
        ]
        if not selected_edges:
            return {
                "ok": False,
                "error": f"edge '{selected_edge_name}' was not found in graph.edges.",
                "checked_edges": 0,
                "results": [],
                "edge_name": selected_edge_name,
            }
    if not selected_edges:
        return {"ok": True, "checked_edges": 0, "results": []}

    results: list[dict[str, Any]] = []
    total_edges = len(selected_edges)
    estimated_per_edge_sec = float(GRAPH_PING_DURATION_SEC_DEFAULT) + GRAPH_PING_ESTIMATED_OVERHEAD_SEC
    overall_ok = True
    for idx, edge in enumerate(selected_edges):
        edge_index = idx + 1
        estimated_remaining_sec = int(math.ceil(max(0.0, (total_edges - idx) * estimated_per_edge_sec)))
        if not isinstance(edge, dict):
            item = {
                "ok": False,
                "edge_name": f"edge-{edge_index}",
                "status": "FAILED",
                "message": f"graph.edges[{idx}] must be an object.",
            }
            results.append(item)
            overall_ok = False
            if emit:
                emit("graph_check_failed", **item)
            continue

        source = str(edge.get("from") or "").strip()
        dest = str(edge.get("to") or "").strip()
        edge_name = str(edge.get("name") or f"edge-{edge_index}").strip()
        if not source or not dest:
            item = {
                "ok": False,
                "edge_name": edge_name,
                "source_node": source,
                "target_node": dest,
                "status": "FAILED",
                "message": "Edge requires non-empty 'from' and 'to'.",
            }
            results.append(item)
            overall_ok = False
            if emit:
                emit("graph_check_failed", **item)
            continue

        source_vm = inventory_by_node.get(source)
        dest_vm = inventory_by_node.get(dest)
        if source_vm is None or dest_vm is None:
            item = {
                "ok": False,
                "edge_name": edge_name,
                "source_node": source,
                "target_node": dest,
                "status": "FAILED",
                "message": "Source or destination VM node is missing from inventory.",
            }
            results.append(item)
            overall_ok = False
            if emit:
                emit("graph_check_failed", **item)
            continue

        dest_address = str(dest_vm.get("address") or "")
        if not dest_address:
            item = {
                "ok": False,
                "edge_name": edge_name,
                "source_node": source,
                "target_node": dest,
                "status": "FAILED",
                "message": "Destination VM address is missing.",
            }
            results.append(item)
            overall_ok = False
            if emit:
                emit("graph_check_failed", **item)
            continue

        action_payload = {
            "type": "check_graph_ping",
            "source_node": source,
            "target_node": dest,
            "destination_address": dest_address,
            "duration_sec": GRAPH_PING_DURATION_SEC_DEFAULT,
            "step_sec": GRAPH_PING_STEP_SEC_DEFAULT,
            "wait_sec": GRAPH_PING_WAIT_SEC_DEFAULT,
            "timeout_sec": GRAPH_PING_TIMEOUT_SEC_DEFAULT,
        }
        if emit:
            emit(
                "graph_checking",
                edge_name=edge_name,
                edge_index=edge_index,
                total_edges=total_edges,
                source=source,
                destination=dest,
                estimated_per_edge_sec=round(estimated_per_edge_sec, 1),
                estimated_remaining_sec=estimated_remaining_sec,
                duration_sec=action_payload["duration_sec"],
                step_sec=action_payload["step_sec"],
                wait_sec=action_payload["wait_sec"],
                timeout_sec=action_payload["timeout_sec"],
                action=action_payload,
            )
        edge_started_at = time.perf_counter()
        ctx = ExecutionContext(
            inventory_by_node={source: source_vm, dest: dest_vm},
            action=action_payload,
        )
        try:
            outcome = GraphPingCheckExec(ctx).execute()
        except ActionExecutionError as exc:
            item = {
                "ok": False,
                "edge_name": edge_name,
                "source_node": source,
                "target_node": dest,
                "status": "FAILED",
                "message": str(exc),
                "action": action_payload,
            }
            results.append(item)
            overall_ok = False
            if emit:
                emit("graph_check_failed", **item)
            continue

        elapsed_sec = round(time.perf_counter() - edge_started_at, 3)
        metrics = outcome.metrics if isinstance(outcome.metrics, dict) else {}
        latency_avg_ms = metrics.get("latency_avg_ms")
        item = {
            "ok": bool(outcome.success),
            "edge_name": edge_name,
            "source_node": source,
            "target_node": dest,
            "status": outcome.status,
            "message": outcome.message,
            "elapsed_sec": elapsed_sec,
            "command": outcome.debug.get("command", ""),
            "metrics": metrics,
            "debug": outcome.debug if isinstance(outcome.debug, dict) else {},
        }
        results.append(item)
        overall_ok = overall_ok and bool(outcome.success)
        if emit:
            emit(
                "graph_checked",
                **item,
                edge_index=edge_index,
                total_edges=total_edges,
                latency_avg_ms=latency_avg_ms,
                logs=outcome.logs,
            )

        source_name = str(source_vm.get("name") or source)
        if source_name and user_id is not None:
            _record_ssh_access_history(
                user_id=user_id,
                vm_name=source_name,
                scenario_name=scenario_name,
                phase_name=phase_name,
                action_type="check_graph_ping",
                command=str(outcome.debug.get("command") or "ping"),
                success=bool(outcome.success),
                returncode=int(outcome.debug.get("returncode") or (0 if outcome.success else 1)),
                stdout="\n".join(outcome.logs) if isinstance(outcome.logs, list) else "",
                stderr=str(outcome.debug.get("stderr") or ""),
                source="celery_task:graph_check",
            )

    return {
        "ok": overall_ok,
        "checked_edges": len(results),
        "results": results,
        "edge_name": selected_edge_name or "",
    }


def _check_status_from_result(ok: bool, requested: bool = True, current_status: str | None = None) -> str:
    if not requested:
        return current_status or Scenario.CheckStatus.UNKNOWN
    return Scenario.CheckStatus.PASS if ok else Scenario.CheckStatus.FAIL


def _append_validation_history(
    scenario: Scenario,
    *,
    task_id: str,
    phase_name: str | None,
) -> None:
    history = scenario.validation_history if isinstance(scenario.validation_history, list) else []
    run_index = len(history) + 1

    history_entry = {
        "run": run_index,
        "task_id": task_id,
        "phase_name": phase_name,
        "validated_at": scenario.last_validation_at.isoformat() if scenario.last_validation_at else "",
        "entries": [
            {"action_name": "actions", "status": scenario.check_status_actions},
            {"action_name": "node", "status": scenario.check_status_node},
            {"action_name": "graph", "status": scenario.check_status_graph},
        ],
    }
    history.append(history_entry)
    scenario.validation_history = history[-100:]


def _append_validation_trace(scenario: Scenario, step: str, **meta: Any) -> None:
    trace = scenario.validation_trace if isinstance(scenario.validation_trace, list) else []
    trace_entry = {"time": timezone.now().isoformat(), "step": step, **meta}
    trace.append(trace_entry)
    scenario.validation_trace = trace[-300:]
    current = scenario.last_validation_data if isinstance(scenario.last_validation_data, dict) else {}
    current["progress_trace"] = scenario.validation_trace
    scenario.last_validation_data = current
    scenario.save(update_fields=["validation_trace", "last_validation_data", "updated_at"])


def _append_run_trace(run: ScenarioValidationRun, step: str, **meta: Any) -> None:
    trace = run.trace if isinstance(run.trace, list) else []
    trace_entry = {"time": timezone.now().isoformat(), "step": step, **meta}
    trace.append(trace_entry)
    run.trace = trace[-300:]
    run.last_heartbeat_at = timezone.now()
    run.save(update_fields=["trace", "last_heartbeat_at"])


@shared_task(bind=True, soft_time_limit=PHASE_TASK_SOFT_LIMIT_SEC, time_limit=PHASE_TASK_HARD_LIMIT_SEC)
def validate_scenario_phase_task(
    self,
    *,
    user_id: int,
    scenario_id: int,
    phase_name: str | None = None,
    edge_name: str | None = None,
    probe_metrics_folder_name: str | None = None,
    probe_metrics_remote_dir: str | None = None,
    probe_metrics_local_dir: str | None = None,
    current_run_id: int | None = None,
) -> dict:
    _emit_progress(
        self,
        "phase_task_start",
        user_id=user_id,
        scenario_id=scenario_id,
        phase_name=phase_name,
        edge_name=edge_name,
        probe_metrics_folder_name=probe_metrics_folder_name,
        probe_metrics_remote_dir=probe_metrics_remote_dir,
        probe_metrics_local_dir=probe_metrics_local_dir,
        current_run_id=current_run_id,
    )
    scenario = Scenario.objects.get(id=scenario_id, created_by_id=user_id)
    spec = _get_scenario_spec(scenario)
    _emit_progress(
        self,
        "phase_task_loaded",
        scenario_name=scenario.name,
        node_count=len(scenario.nodes if isinstance(scenario.nodes, list) else []),
        phase_template_count=len(spec.get("phaseTemplates") or []) if isinstance(spec.get("phaseTemplates"), list) else 0,
    )
    runtime_env_payload, runtime_error = _resolve_runtime_env_payload(user_id, scenario)
    if runtime_error:
        _emit_progress(
            self,
            "phase_task_runtime_env_missing",
            phase_name=phase_name,
            error=runtime_error,
        )
    validation_result = _validate_actions(spec, phase_name, runtime_env_payload=runtime_env_payload, edge_name=edge_name)
    _emit_progress(
        self,
        "phase_task_validation_result",
        phase_name=phase_name,
        ok=validation_result.get("ok"),
        checked_actions=validation_result.get("checked_actions"),
        error_count=len(validation_result.get("errors") or []),
    )
    if runtime_error:
        validation_result["ok"] = False
        validation_result["errors"] = list(validation_result.get("errors") or []) + [runtime_error]
    execution_result: dict[str, Any] = {
        "ok": True,
        "phase_name": phase_name,
        "executed_actions": 0,
        "results": [],
        "errors": [],
        "skipped": True,
    }
    if phase_name and validation_result.get("ok"):
        execution_result = _execute_phase_actions(
            user_id,
            scenario,
            spec=spec,
            phase_name=phase_name,
            edge_name=edge_name,
            probe_metrics_folder_name=probe_metrics_folder_name,
            probe_metrics_remote_dir=probe_metrics_remote_dir,
            probe_metrics_local_dir=probe_metrics_local_dir,
            current_run_id=current_run_id,
            emit=lambda step, **meta: _emit_progress(self, step, **meta),
        )
        execution_result["skipped"] = False
    else:
        _emit_progress(
            self,
            "phase_task_execution_skipped",
            phase_name=phase_name,
            reason=(
                "phase_name missing"
                if not phase_name
                else "validation_failed"
            ),
        )
    result = {
        **validation_result,
        "execution": execution_result,
        "ok": bool(validation_result.get("ok")) and bool(execution_result.get("ok")),
    }
    _emit_progress(
        self,
        "phase_task_done",
        ok=result.get("ok"),
        checked_actions=result.get("checked_actions"),
        executed_actions=execution_result.get("executed_actions"),
    )
    return result


@shared_task(bind=True, soft_time_limit=NODE_TASK_SOFT_LIMIT_SEC, time_limit=NODE_TASK_HARD_LIMIT_SEC)
def validate_scenario_node_task(
    self,
    *,
    user_id: int,
    scenario_id: int,
    phase_name: str | None = None,
    edge_name: str | None = None,
) -> dict:
    _emit_progress(
        self,
        "node_task_start",
        user_id=user_id,
        scenario_id=scenario_id,
        phase_name=phase_name,
        edge_name=edge_name,
    )
    scenario = Scenario.objects.get(id=scenario_id, created_by_id=user_id)
    spec = _get_scenario_spec(scenario)
    required_targets = (
        _collect_target_nodes_for_phase(spec, phase_name, edge_name=edge_name)
        if phase_name
        else set()
    )
    inventory_by_node, inventory_errors = _build_inventory_by_node(user_id, scenario)
    if inventory_errors:
        result = {"ok": False, "results": [], "errors": inventory_errors, "phase_name": phase_name}
        _emit_progress(self, "node_task_done", ok=False, errors=inventory_errors)
        return result
    result = _check_nodes_connectivity(
        inventory_by_node,
        required_targets,
        phase_name,
        emit=lambda step, **meta: _emit_progress(self, step, **meta),
        user_id=user_id,
        scenario_name=scenario.name,
    )
    _emit_progress(self, "node_task_done", ok=result.get("ok"))
    return result


@shared_task(bind=True, soft_time_limit=GRAPH_TASK_SOFT_LIMIT_SEC, time_limit=GRAPH_TASK_HARD_LIMIT_SEC)
def validate_scenario_graph_task(
    self,
    *,
    user_id: int,
    scenario_id: int,
    edge_name: str | None = None,
) -> dict:
    task_started_at = time.perf_counter()
    scenario = Scenario.objects.get(id=scenario_id, created_by_id=user_id)
    inventory_by_node, inventory_errors = _build_inventory_by_node(user_id, scenario)
    if inventory_errors:
        result = {"ok": False, "error": "Inventory resolution failed.", "details": inventory_errors}
        result["summary"] = scenario.get_graph_check_summary(
            {"check_graph": True, "checks": {"graph": result}}
        )
        _emit_progress(self, "graph_task_done", ok=False, errors=inventory_errors)
        return result
    graph = scenario.graph if isinstance(scenario.graph, dict) else {}
    graph_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    selected_edge_name = (edge_name or "").strip()
    selected_edges = graph_edges
    if selected_edge_name:
        selected_edges = [
            edge
            for edge in graph_edges
            if isinstance(edge, dict) and str(edge.get("name") or "").strip() == selected_edge_name
        ]
        if not selected_edges:
            result = {
                "ok": False,
                "error": f"edge '{selected_edge_name}' was not found in graph.edges.",
                "checked_edges": 0,
                "results": [],
                "edge_name": selected_edge_name,
            }
            result["summary"] = scenario.get_graph_check_summary({"check_graph": True, "checks": {"graph": result}})
            _emit_progress(self, "graph_task_done", ok=False, edge_name=selected_edge_name, error=result["error"])
            return result
    estimated_per_edge_sec = float(GRAPH_PING_DURATION_SEC_DEFAULT) + GRAPH_PING_ESTIMATED_OVERHEAD_SEC
    estimated_total_sec = int(math.ceil(len(selected_edges) * estimated_per_edge_sec))
    _emit_progress(
        self,
        "graph_task_start",
        user_id=user_id,
        scenario_id=scenario_id,
        scenario_name=scenario.name,
        total_edges=len(selected_edges),
        edge_name=selected_edge_name or "",
        duration_sec=GRAPH_PING_DURATION_SEC_DEFAULT,
        step_sec=GRAPH_PING_STEP_SEC_DEFAULT,
        wait_sec=GRAPH_PING_WAIT_SEC_DEFAULT,
        timeout_sec=GRAPH_PING_TIMEOUT_SEC_DEFAULT,
        estimated_per_edge_sec=round(estimated_per_edge_sec, 1),
        estimated_total_sec=estimated_total_sec,
    )
    result = _check_graph_ping(
        inventory_by_node,
        selected_edges,
        emit=lambda step, **meta: _emit_progress(self, step, **meta),
        user_id=user_id,
        scenario_name=scenario.name,
        edge_name=selected_edge_name,
    )
    result["summary"] = scenario.get_graph_check_summary(
        {"check_graph": True, "checks": {"graph": result}}
    )
    total_elapsed_sec = round(time.perf_counter() - task_started_at, 3)
    _emit_progress(
        self,
        "graph_task_done",
        ok=result.get("ok"),
        total_edges=len(selected_edges),
        edge_name=selected_edge_name or "",
        elapsed_sec=total_elapsed_sec,
        estimated_total_sec=estimated_total_sec,
    )
    return result


@shared_task(bind=True, soft_time_limit=VALIDATE_TASK_SOFT_LIMIT_SEC, time_limit=VALIDATE_TASK_HARD_LIMIT_SEC)
def validate_scenario_task(
    self,
    *,
    user_id: int,
    scenario_id: int,
    run_id: int | None = None,
    check_actions: bool = True,
    check_nodes: bool = True,
    check_graph: bool = True,
    phase_name: str | None = None,
    edge_name: str | None = None,
    **extra_kwargs: Any,
) -> dict:
    def emit(step: str, **meta: Any) -> None:
        _emit_progress(self, step, **meta)
        _append_validation_trace(scenario, step, **meta)
        _append_run_trace(run, step, **meta)

    _emit_progress(
        self,
        "start",
        user_id=user_id,
        scenario_id=scenario_id,
        check_actions=check_actions,
        check_nodes=check_nodes,
        check_graph=check_graph,
        phase_name=phase_name,
        edge_name=edge_name,
    )
    if extra_kwargs:
        _emit_progress(
            self,
            "start_ignored_kwargs",
            keys=sorted(str(key) for key in extra_kwargs.keys()),
        )
    scenario = Scenario.objects.get(id=scenario_id, created_by_id=user_id)
    probe_metrics_folder_name = (
        _build_validate_folder_name()
        if check_actions and (phase_name == _PROBE_PHASE_NAME)
        else None
    )
    probe_metrics_remote_dir = (
        _build_validate_remote_dir(probe_metrics_folder_name)
        if probe_metrics_folder_name
        else ""
    )
    probe_metrics_local_dir = (
        str(_build_validate_local_dir(probe_metrics_folder_name))
        if probe_metrics_folder_name
        else ""
    )
    if run_id is not None:
        run = ScenarioValidationRun.objects.get(id=run_id, scenario_id=scenario_id, requested_by_id=user_id)
    else:
        run = ScenarioValidationRun.objects.create(
            scenario=scenario,
            requested_by_id=user_id,
            check_actions=check_actions,
            check_nodes=check_nodes,
            check_graph=check_graph,
            phase_name=phase_name or "",
            status=ScenarioValidationRun.RunStatus.PENDING,
            result_data={},
        )
    _emit_progress(
        self,
        "run_context_ready",
        scenario_name=scenario.name,
        run_id=run.id,
        phase_name=phase_name,
        edge_name=edge_name,
        check_actions=check_actions,
        check_nodes=check_nodes,
        check_graph=check_graph,
        probe_metrics_folder_name=probe_metrics_folder_name,
        probe_metrics_remote_dir=probe_metrics_remote_dir,
        probe_metrics_local_dir=probe_metrics_local_dir,
    )
    _append_validation_trace(
        scenario,
        "start",
        user_id=user_id,
        scenario_id=scenario_id,
        check_actions=check_actions,
        check_nodes=check_nodes,
        check_graph=check_graph,
        phase_name=phase_name,
        edge_name=edge_name,
        probe_metrics_folder_name=probe_metrics_folder_name,
        probe_metrics_remote_dir=probe_metrics_remote_dir,
    )

    scenario.validation_requested = True
    scenario.check_status_actions = (
        Scenario.CheckStatus.RUNNING if check_actions else (scenario.check_status_actions or Scenario.CheckStatus.UNKNOWN)
    )
    scenario.check_status_node = (
        Scenario.CheckStatus.RUNNING if check_nodes else (scenario.check_status_node or Scenario.CheckStatus.UNKNOWN)
    )
    scenario.check_status_graph = (
        Scenario.CheckStatus.RUNNING if check_graph else (scenario.check_status_graph or Scenario.CheckStatus.UNKNOWN)
    )
    scenario.validation_status = scenario.compute_validation_status()
    scenario.last_validation_task_id = self.request.id or ""
    scenario.modified_by_id = user_id
    scenario.last_validation_data = {
        "run_id": run.id,
        "check_actions": check_actions,
        "check_nodes": check_nodes,
        "check_graph": check_graph,
        "phase_name": phase_name,
        "edge_name": edge_name,
        "task_id": self.request.id,
        "status": Scenario.ValidationStatus.RUNNING,
        "probe_metrics_folder_name": probe_metrics_folder_name,
        "probe_metrics_remote_dir": probe_metrics_remote_dir,
        "probe_metrics_local_dir": probe_metrics_local_dir,
        "progress_trace": scenario.validation_trace if isinstance(scenario.validation_trace, list) else [],
    }
    scenario.last_validation_data["failure_report"] = scenario.build_failure_report(
        scenario.last_validation_data
    )
    scenario.save(
        update_fields=[
            "validation_requested",
            "validation_status",
            "check_status_actions",
            "check_status_node",
            "check_status_graph",
            "last_validation_task_id",
            "last_validation_data",
            "modified_by",
            "updated_at",
        ]
    )
    run.status = ScenarioValidationRun.RunStatus.RUNNING
    run.started_at = timezone.now()
    run.last_heartbeat_at = timezone.now()
    run.task_id = self.request.id or run.task_id
    run.result_data = {
        **(run.result_data if isinstance(run.result_data, dict) else {}),
        "run_id": run.id,
        "check_actions": check_actions,
        "check_nodes": check_nodes,
        "check_graph": check_graph,
        "phase_name": phase_name,
        "edge_name": edge_name,
        "task_id": run.task_id,
        "status": ScenarioValidationRun.RunStatus.RUNNING,
        "probe_metrics_folder_name": probe_metrics_folder_name,
        "probe_metrics_remote_dir": probe_metrics_remote_dir,
        "probe_metrics_local_dir": probe_metrics_local_dir,
    }
    run.probe_metrics = {}
    run.metrics_remote_dir = probe_metrics_remote_dir
    run.metrics_local_dir = probe_metrics_local_dir
    run.need_to_collect_metrics = bool(probe_metrics_folder_name)
    run.save(
        update_fields=[
            "status",
            "started_at",
            "last_heartbeat_at",
            "task_id",
            "result_data",
            "metrics_remote_dir",
            "metrics_local_dir",
            "need_to_collect_metrics",
            "probe_metrics",
        ]
    )
    emit("state_saved_running", scenario_name=scenario.name)

    try:
        actions_result: dict[str, Any] = {
            "ok": True,
            "skipped": not check_actions,
            "checked_phases": [],
            "checked_actions": 0,
            "errors": [],
        }
        if check_actions:
            emit("phase_validation_start")
            actions_result = validate_scenario_phase_task.run(
                user_id=user_id,
                scenario_id=scenario_id,
                phase_name=phase_name,
                edge_name=edge_name,
                probe_metrics_folder_name=probe_metrics_folder_name,
                probe_metrics_remote_dir=probe_metrics_remote_dir,
                probe_metrics_local_dir=probe_metrics_local_dir,
                current_run_id=run.id,
            )
            emit(
                "phase_validation_done",
                ok=actions_result.get("ok"),
                checked_actions=actions_result.get("checked_actions"),
                checked_phases=actions_result.get("checked_phases"),
                executed_actions=((actions_result.get("execution") or {}).get("executed_actions")),
                error_count=len(actions_result.get("errors") or []),
            )

        nodes_result: dict[str, Any] = {
            "ok": True,
            "skipped": not check_nodes,
            "results": [],
            "phase_name": phase_name,
        }
        if check_nodes:
            emit("node_check_start")
            nodes_result = validate_scenario_node_task.run(
                user_id=user_id,
                scenario_id=scenario_id,
                phase_name=phase_name,
                edge_name=edge_name,
            )
            emit(
                "node_check_done",
                ok=nodes_result.get("ok"),
                checked_nodes=len(nodes_result.get("results") or []),
                error_count=len(nodes_result.get("errors") or []),
            )

        graph_result: dict[str, Any] = {"ok": True, "skipped": not check_graph}
        if check_graph:
            emit("graph_check_start")
            graph_result = validate_scenario_graph_task.run(
                user_id=user_id,
                scenario_id=scenario_id,
                edge_name=edge_name,
            )
            emit(
                "graph_check_done",
                ok=graph_result.get("ok"),
                checked_edges=graph_result.get("checked_edges"),
                error=graph_result.get("error"),
            )
        graph_summary = scenario.get_graph_check_summary(
            {"check_graph": check_graph, "checks": {"graph": graph_result}}
        )
        graph_result["summary"] = graph_summary

        overall_ok = True
        if check_actions:
            overall_ok = overall_ok and bool(actions_result.get("ok"))
        if check_nodes:
            overall_ok = overall_ok and bool(nodes_result.get("ok"))
        if check_graph:
            overall_ok = overall_ok and bool(graph_result.get("ok"))

        probe_metrics = {}
        if check_actions:
            execution_payload = actions_result.get("execution") or {}
            if isinstance(execution_payload, dict):
                probe_metrics = execution_payload.get("probe_metrics") or {}

        payload = {
            "scenario": scenario.name,
            "phase_name": phase_name,
            "edge_name": edge_name,
            "ok": overall_ok,
            "checks": {
                "actions": actions_result,
                "nodes": nodes_result,
                "graph": graph_result,
            },
            "probe_metrics": probe_metrics if isinstance(probe_metrics, dict) else {},
            "graph_check_summary": graph_summary,
        }
        payload["failure_report"] = scenario.build_failure_report(payload)

        scenario.last_validation_data = payload
        scenario.last_validation_at = timezone.now()
        scenario.check_status_actions = _check_status_from_result(
            bool(actions_result.get("ok")),
            check_actions,
            scenario.check_status_actions,
        )
        scenario.check_status_node = _check_status_from_result(
            bool(nodes_result.get("ok")),
            check_nodes,
            scenario.check_status_node,
        )
        scenario.check_status_graph = _check_status_from_result(
            bool(graph_result.get("ok")),
            check_graph,
            scenario.check_status_graph,
        )
        scenario.validation_status = scenario.compute_validation_status()
        _append_validation_history(
            scenario,
            task_id=(self.request.id or ""),
            phase_name=phase_name,
        )
        scenario.modified_by_id = user_id
        scenario.save(
            update_fields=[
                "last_validation_data",
                "last_validation_at",
                "validation_status",
                "check_status_actions",
                "check_status_node",
                "check_status_graph",
                "validation_history",
                "modified_by",
                "updated_at",
            ]
        )
        run.status = ScenarioValidationRun.RunStatus.SUCCEEDED if overall_ok else ScenarioValidationRun.RunStatus.FAILED
        run.finished_at = timezone.now()
        run.last_heartbeat_at = timezone.now()
        run.result_data = payload
        run.probe_metrics = probe_metrics if isinstance(probe_metrics, dict) else {}
        if isinstance(probe_metrics, dict):
            run.metrics_remote_dir = str(probe_metrics.get("remote_dir") or run.metrics_remote_dir or "")
            run.metrics_local_dir = str(probe_metrics.get("local_dir") or run.metrics_local_dir or "")
        if phase_name == _PROBE_PHASE_NAME:
            run.need_to_collect_metrics = bool(
                overall_ok
                and isinstance(probe_metrics, dict)
                and bool(probe_metrics.get("needs_collection"))
            )
        elif phase_name in _COLLECT_PHASE_NAMES:
            run.need_to_collect_metrics = False
        run.error_message = ""
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "last_heartbeat_at",
                "result_data",
                "metrics_remote_dir",
                "metrics_local_dir",
                "need_to_collect_metrics",
                "probe_metrics",
                "error_message",
            ]
        )
        _upsert_run_vm_metrics(
            run=run,
            user_id=user_id,
            phase_name=phase_name or "",
            probe_metrics=run.probe_metrics,
        )
        emit("completed", ok=overall_ok, validation_status=scenario.validation_status)
        _emit_progress(
            self,
            "run_context_completed",
            scenario_name=scenario.name,
            run_id=run.id,
            ok=overall_ok,
            validation_status=scenario.validation_status,
            error_count=(
                len(actions_result.get("errors") or [])
                + len(nodes_result.get("errors") or [])
                + (1 if graph_result.get("error") else 0)
            ),
        )
        return payload
    except Exception as exc:
        emit("failed", error=str(exc))
        _emit_progress(
            self,
            "run_context_failed",
            scenario_name=scenario.name,
            run_id=run.id,
            error=str(exc),
        )
        scenario.last_validation_at = timezone.now()
        if check_actions:
            scenario.check_status_actions = Scenario.CheckStatus.FAIL
        if check_nodes:
            scenario.check_status_node = Scenario.CheckStatus.FAIL
        if check_graph:
            scenario.check_status_graph = Scenario.CheckStatus.FAIL
        scenario.validation_status = scenario.compute_validation_status()
        scenario.last_validation_data = {
            "ok": False,
            "error": str(exc),
            "check_actions": check_actions,
            "check_nodes": check_nodes,
            "check_graph": check_graph,
            "phase_name": phase_name,
            "edge_name": edge_name,
            "task_id": self.request.id,
            "progress_trace": scenario.validation_trace if isinstance(scenario.validation_trace, list) else [],
        }
        scenario.last_validation_data["graph_check_summary"] = scenario.get_graph_check_summary(
            scenario.last_validation_data
        )
        scenario.last_validation_data["failure_report"] = scenario.build_failure_report(
            scenario.last_validation_data
        )
        _append_validation_history(
            scenario,
            task_id=(self.request.id or ""),
            phase_name=phase_name,
        )
        scenario.modified_by_id = user_id
        scenario.save(
            update_fields=[
                "last_validation_at",
                "validation_status",
                "check_status_actions",
                "check_status_node",
                "check_status_graph",
                "last_validation_data",
                "validation_history",
                "modified_by",
                "updated_at",
            ]
        )
        run.status = ScenarioValidationRun.RunStatus.FAILED
        run.finished_at = timezone.now()
        run.last_heartbeat_at = timezone.now()
        run.error_message = str(exc)
        probe_metrics = {}
        actions_payload = locals().get("actions_result")
        if isinstance(actions_payload, dict):
            execution_payload = actions_payload.get("execution") or {}
            if isinstance(execution_payload, dict):
                probe_metrics = execution_payload.get("probe_metrics") or {}
        run.result_data = {
            **(run.result_data if isinstance(run.result_data, dict) else {}),
            "ok": False,
            "error": str(exc),
            "status": ScenarioValidationRun.RunStatus.FAILED,
            "failure_report": scenario.last_validation_data.get("failure_report"),
        }
        run.probe_metrics = probe_metrics if isinstance(probe_metrics, dict) else {}
        if isinstance(probe_metrics, dict):
            run.metrics_remote_dir = str(probe_metrics.get("remote_dir") or run.metrics_remote_dir or "")
            run.metrics_local_dir = str(probe_metrics.get("local_dir") or run.metrics_local_dir or "")
        if phase_name == _PROBE_PHASE_NAME:
            run.need_to_collect_metrics = False
        elif phase_name in _COLLECT_PHASE_NAMES:
            run.need_to_collect_metrics = False
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "last_heartbeat_at",
                "error_message",
                "result_data",
                "metrics_remote_dir",
                "metrics_local_dir",
                "need_to_collect_metrics",
                "probe_metrics",
            ]
        )
        raise

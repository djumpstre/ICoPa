"""Celery tasks for generated profiling experiment execution."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone
import yaml

from inventory.models import VM
from icopa_core.task_executor import ActionExecutionError, ExecutionContext, build_executor
from runtime_env.models import RuntimeEnvironment

from .models import ProfilingRun


def _node_vm_candidates(node: dict[str, Any]) -> list[str]:
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


def _resolve_vm_for_node(user_id: int, node: dict[str, Any]) -> VM | None:
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
        "container_runtime_ready": bool(vm.container_runtime_ready),
        "container_runtime_type": str(vm.container_runtime_type or ""),
        "last_connection_time": vm.last_connection_time.isoformat() if vm.last_connection_time else "",
        "metadata": vm.metadata if isinstance(vm.metadata, dict) else {},
        "networking": vm.networking if isinstance(vm.networking, dict) else {},
        "credential": {"key_path": key_path},
    }


def _scenario_spec_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    spec = snapshot.get("spec")
    if isinstance(spec, dict):
        return spec
    return snapshot


def _build_inventory_by_node(user_id: int, scenario_snapshot: dict[str, Any]) -> tuple[dict[str, dict], list[str]]:
    inventory_by_node: dict[str, dict] = {}
    errors: list[str] = []

    scenario_spec = _scenario_spec_from_snapshot(scenario_snapshot)
    nodes = scenario_spec.get("nodes") if isinstance(scenario_spec.get("nodes"), list) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_name = str(node.get("nodeName") or node.get("name") or "")
        node_kind = str(node.get("kind") or node.get("type") or "").lower()
        if not node_name or node_kind != "vm":
            continue

        vm = _resolve_vm_for_node(user_id, node)
        if vm is None:
            errors.append(f"Inventory VM not found for node {node_name}.")
            continue
        if vm.credential is None:
            errors.append(f"VM {vm.name} for node {node_name} has no SSH credential.")
            continue
        inventory_by_node[node_name] = _vm_to_inventory_payload(vm)
    return inventory_by_node, errors


def _resolve_runtime_env_payload(user_id: int, runtime_env_name: str) -> tuple[dict[str, Any], str | None]:
    runtime_name = str(runtime_env_name or "").strip()
    if not runtime_name:
        return {}, "runtime_env_name is required for generated run execution."

    runtime_env = RuntimeEnvironment.objects.filter(created_by_id=user_id, name=runtime_name).first()
    if runtime_env is None:
        return {}, f"Runtime environment {runtime_name} not found."

    return (
        {
            "name": runtime_env.name,
            "images": runtime_env.images if isinstance(runtime_env.images, dict) else {},
            "tags": runtime_env.tags if isinstance(runtime_env.tags, dict) else {},
            "parameters": runtime_env.parameters if isinstance(runtime_env.parameters, dict) else {},
            "command_preset": runtime_env.command_preset if isinstance(runtime_env.command_preset, list) else [],
            "commandPresets": runtime_env.command_preset if isinstance(runtime_env.command_preset, list) else [],
            "command_groups": runtime_env.command_groups if isinstance(runtime_env.command_groups, dict) else {},
            "serializer_rrt_config_raw_yaml": str(runtime_env.serializer_rrt_config_raw_yaml or ""),
            "serializer_rrt_config_path": str(runtime_env.serializer_rrt_config_path or ""),
            "uploaded_version": int(runtime_env.uploaded_version or 0),
        },
        None,
    )


def _execute_one_action(
    *,
    inventory_by_node: dict[str, dict],
    runtime_env_payload: dict[str, Any],
    scenario_snapshot: dict[str, Any],
    experiment_snapshot: dict[str, Any],
    phase: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    ctx = ExecutionContext(
        inventory_by_node=inventory_by_node,
        runtime_env=runtime_env_payload,
        scenario=scenario_snapshot,
        experiment=experiment_snapshot,
        phase={"name": str(phase.get("name") or "")},
        action=action,
    )
    outcome = build_executor(ctx).execute()
    return {
        "type": str(action.get("type") or ""),
        "target": str(action.get("target") or ""),
        "status": outcome.status,
        "success": outcome.success,
        "message": outcome.message,
        "debug": outcome.debug,
    }


def _with_cached_connectivity_runtime_flag(
    *,
    action: dict[str, Any],
    runtime_verified_nodes: set[str],
) -> dict[str, Any]:
    if str(action.get("type") or "") != "check_connectivity":
        return action
    target = str(action.get("target") or "").strip()
    if not target or target not in runtime_verified_nodes:
        return action
    parameters = action.get("parameters")
    if isinstance(parameters, dict) and "check_container_runtime" in parameters:
        return action
    prepared = deepcopy(action)
    prepared_params = dict(parameters) if isinstance(parameters, dict) else {}
    prepared_params["check_container_runtime"] = False
    prepared["parameters"] = prepared_params
    return prepared


def _sync_connectivity_result_to_inventory(
    *,
    user_id: int,
    run_id: str,
    action: dict[str, Any],
    action_result: dict[str, Any],
    inventory_by_node: dict[str, dict[str, Any]],
    runtime_verified_nodes: set[str],
) -> None:
    if str(action.get("type") or "") != "check_connectivity":
        return
    target_node = str(action.get("target") or action_result.get("target") or "").strip()
    if not target_node:
        return
    vm_payload = inventory_by_node.get(target_node)
    if not isinstance(vm_payload, dict):
        return
    vm_name = str(vm_payload.get("name") or target_node).strip()
    if not vm_name:
        return

    vm = VM.objects.filter(created_by_id=user_id, name=vm_name).first()
    if vm is None:
        return

    parameters = action.get("parameters")
    check_container_runtime = True
    if isinstance(parameters, dict) and "check_container_runtime" in parameters:
        check_container_runtime = bool(parameters.get("check_container_runtime"))

    now = timezone.now()
    success = bool(action_result.get("success"))
    metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
    debug_payload = action_result.get("debug")
    debug = debug_payload if isinstance(debug_payload, dict) else {}
    error_text = str(action_result.get("message") or debug.get("stderr") or "").strip()

    vm.last_connection_time = now
    vm.status = VM.VMStatus.ACTIVE if success else VM.VMStatus.ERROR
    if check_container_runtime:
        vm.container_runtime_ready = success
        vm.container_runtime_type = "docker" if success else ""

    metadata["last_connectivity_check"] = {
        "ok": success,
        "run_id": run_id,
        "target_node": target_node,
        "checked_at": now.isoformat(),
        "check_container_runtime": check_container_runtime,
        "error": error_text,
    }
    if check_container_runtime:
        metadata["container_runtime_last_check"] = {
            "ok": success,
            "run_id": run_id,
            "target_node": target_node,
            "checked_at": now.isoformat(),
            "runtime_type": "docker" if success else "",
            "error": error_text,
        }
    vm.metadata = metadata
    vm.modified_by_id = user_id

    update_fields = ["last_connection_time", "status", "metadata", "modified_by", "updated_at"]
    if check_container_runtime:
        update_fields.extend(["container_runtime_ready", "container_runtime_type"])
    vm.save(update_fields=update_fields)

    if success and check_container_runtime:
        runtime_verified_nodes.add(target_node)
    if not success:
        runtime_verified_nodes.discard(target_node)

    vm_payload["status"] = vm.status
    vm_payload["metadata"] = metadata
    vm_payload["container_runtime_ready"] = vm.container_runtime_ready
    vm_payload["container_runtime_type"] = vm.container_runtime_type
    vm_payload["last_connection_time"] = vm.last_connection_time.isoformat() if vm.last_connection_time else ""


def _join_path(base: str, leaf: str) -> str:
    base_text = str(base or "").rstrip("/")
    leaf_text = str(leaf or "").strip("/")
    if not base_text:
        return leaf_text
    if not leaf_text:
        return base_text
    return f"{base_text}/{leaf_text}"


def _prepare_action_payload_for_execution(
    *,
    action: dict[str, Any],
    inventory_by_node: dict[str, dict[str, Any]],
    run_metrics_remote_dir: str,
    run_metrics_backend_dir: str,
    run_variant_values: dict[str, Any] | None = None,
    run_rrt_config_yaml: str = "",
) -> dict[str, Any]:
    prepared = deepcopy(action)
    action_type = str(prepared.get("type") or "")
    if action_type == "run_runtime_preset":
        preset = str(prepared.get("preset") or "").strip()
        if preset.startswith("profiling/"):
            params = prepared.get("parameters")
            if not isinstance(params, dict):
                params = {}
            force_replace = str(
                params.get("force_replace_metrics_dir", params.get("force_replace_validate_metrics_dir", "true"))
            ).strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
                "on",
            }
            metrics_keys = ("icopa_metrics_dir", "validate_metrics_dir", "validate_metrics_path")
            if force_replace:
                for key in metrics_keys:
                    params[key] = run_metrics_remote_dir
            else:
                for key in metrics_keys:
                    params.setdefault(key, run_metrics_remote_dir)
            auto_rrt_overrides = _variant_values_to_rrt_overrides(run_variant_values)
            if auto_rrt_overrides:
                existing_overrides = params.get("rrt_config_overrides")
                if isinstance(existing_overrides, dict):
                    params["rrt_config_overrides"] = _deep_merge_dict(existing_overrides, auto_rrt_overrides)
                else:
                    params["rrt_config_overrides"] = auto_rrt_overrides
            run_rrt_yaml_text = str(run_rrt_config_yaml or "").strip()
            if run_rrt_yaml_text:
                params["rrt_config_yaml"] = run_rrt_yaml_text
            prepared["parameters"] = params
    if action_type == "collect_metrics":
        target = str(prepared.get("target") or "").strip()
        if not target and inventory_by_node:
            prepared["target"] = sorted(inventory_by_node.keys())[0]
        params = prepared.get("parameters")
        if not isinstance(params, dict):
            params = {}
        params.setdefault("remote_dir", run_metrics_remote_dir)
        params.setdefault("local_base_dir", run_metrics_backend_dir)
        params.setdefault("connect_timeout_sec", 8)
        params.setdefault("scp_timeout_sec", 120)
        prepared["parameters"] = params
    return prepared


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _variant_values_to_rrt_overrides(variant_values: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(variant_values, dict) or not variant_values:
        return {}
    sender_ros: dict[str, Any] = {}
    responder_ros: dict[str, Any] = {}

    sender_map = {
        "payload_size": "payload_size",
        "payloadSize": "payload_size",
        "publish_rate_hz": "frequency_hz",
        "pub_rate_hz": "frequency_hz",
        "frequency_hz": "frequency_hz",
        "json_duration_sec": "json_duration_sec",
    }
    responder_map = {
        "response_payload_size": "response_payload_size",
        "responsePayloadSize": "response_payload_size",
    }
    for key, target_key in sender_map.items():
        if key in variant_values:
            sender_ros[target_key] = variant_values[key]
    for key, target_key in responder_map.items():
        if key in variant_values:
            responder_ros[target_key] = variant_values[key]

    for raw_key, value in variant_values.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if key.startswith("icopa_sender:"):
            tail = key.split(":", 1)[1].strip()
            if tail.startswith("ros__parameters:"):
                tail = tail.split(":", 1)[1].strip()
            if tail:
                sender_ros[tail] = value
            continue
        if key.startswith("icopa_responder:"):
            tail = key.split(":", 1)[1].strip()
            if tail.startswith("ros__parameters:"):
                tail = tail.split(":", 1)[1].strip()
            if tail:
                responder_ros[tail] = value
            continue
        if key.startswith("icopa_sender."):
            tail = key.split(".", 1)[1].strip()
            if tail.startswith("ros__parameters."):
                tail = tail.split(".", 1)[1].strip()
            if tail:
                sender_ros[tail] = value
            continue
        if key.startswith("icopa_responder."):
            tail = key.split(".", 1)[1].strip()
            if tail.startswith("ros__parameters."):
                tail = tail.split(".", 1)[1].strip()
            if tail:
                responder_ros[tail] = value
            continue

    overrides: dict[str, Any] = {}
    if sender_ros:
        overrides["icopa_sender"] = {"ros__parameters": sender_ros}
    if responder_ros:
        overrides["icopa_responder"] = {"ros__parameters": responder_ros}
    return overrides


def _render_effective_rrt_config_yaml(
    *,
    runtime_rrt_yaml: str,
    overrides: dict[str, Any] | None,
) -> str:
    raw_yaml = str(runtime_rrt_yaml or "").strip()
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
            raise ActionExecutionError("serializer_rrt_config_raw_yaml must be a YAML object.")
    merged = _deep_merge_dict(base_payload, override_payload) if override_payload else base_payload
    return yaml.safe_dump(merged, sort_keys=False)


def _now_iso() -> str:
    return timezone.now().isoformat()


def _task_trace(profiling_run_db_id: int, event: str, **meta: Any) -> None:
    line = f"[profiling-run][id:{profiling_run_db_id}] {event}"
    if meta:
        line = f"{line} | {meta}"
    print(line, flush=True)


def _run_phases(run_spec: dict[str, Any]) -> list[dict[str, Any]]:
    phases = run_spec.get("phases")
    if not isinstance(phases, list):
        return []
    return [phase for phase in phases if isinstance(phase, dict)]


def _phase_actions(phase_spec: dict[str, Any]) -> list[dict[str, Any]]:
    actions = phase_spec.get("actions")
    if actions is None and isinstance(phase_spec.get("steps"), list):
        actions = phase_spec.get("steps")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _build_phase_action_tracker(runs: list[Any]) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "last_updated_at": _now_iso(),
        "events": [],
        "runs": {},
    }
    for run_index, run_spec in enumerate(runs, start=1):
        if not isinstance(run_spec, dict):
            continue
        run_id = str(run_spec.get("run_id") or f"run-{run_index:03d}")
        run_entry = {
            "status": "PENDING",
            "runtime_env_name": str(run_spec.get("runtime_env_name") or ""),
            "started_at": "",
            "finished_at": "",
            "error": "",
            "phases": [],
        }
        for phase_index, phase in enumerate(_run_phases(run_spec), start=1):
            phase_entry = {
                "index": phase_index,
                "name": str(phase.get("name") or f"phase-{phase_index}"),
                "status": "PENDING",
                "started_at": "",
                "finished_at": "",
                "error": "",
                "actions": [],
            }
            for action_index, action in enumerate(_phase_actions(phase), start=1):
                phase_entry["actions"].append(
                    {
                        "index": action_index,
                        "type": str(action.get("type") or ""),
                        "target": str(action.get("target") or ""),
                        "status": "PENDING",
                        "started_at": "",
                        "finished_at": "",
                        "message": "",
                        "error": "",
                    }
                )
            run_entry["phases"].append(phase_entry)
        tracker["runs"][run_id] = run_entry
    return tracker


def _append_tracker_event(tracker: dict[str, Any], event: str, **meta: Any) -> None:
    events = tracker.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        tracker["events"] = events
    events.append({"time": _now_iso(), "event": event, **meta})
    if len(events) > 2000:
        del events[:-2000]


def _persist_tracker(run_obj: ProfilingRun, tracker: dict[str, Any]) -> None:
    tracker["last_updated_at"] = _now_iso()
    run_obj.phase_action_execution_status = tracker
    run_obj.save(update_fields=["phase_action_execution_status", "updated_at"])


def _run_entry(tracker: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    runs = tracker.get("runs")
    if not isinstance(runs, dict):
        return None
    entry = runs.get(run_id)
    return entry if isinstance(entry, dict) else None


def _phase_entry(run_entry: dict[str, Any], phase_index: int) -> dict[str, Any] | None:
    phases = run_entry.get("phases")
    if not isinstance(phases, list) or phase_index <= 0 or phase_index > len(phases):
        return None
    phase = phases[phase_index - 1]
    return phase if isinstance(phase, dict) else None


def _action_entry(phase_entry: dict[str, Any], action_index: int) -> dict[str, Any] | None:
    actions = phase_entry.get("actions")
    if not isinstance(actions, list) or action_index <= 0 or action_index > len(actions):
        return None
    action = actions[action_index - 1]
    return action if isinstance(action, dict) else None


def _set_run_status(
    tracker: dict[str, Any],
    run_id: str,
    *,
    status: str,
    error: str = "",
    set_started: bool = False,
    set_finished: bool = False,
) -> None:
    run = _run_entry(tracker, run_id)
    if run is None:
        return
    run["status"] = status
    if set_started:
        run["started_at"] = _now_iso()
    if set_finished:
        run["finished_at"] = _now_iso()
    if error:
        run["error"] = error


def _set_phase_status(
    tracker: dict[str, Any],
    run_id: str,
    phase_index: int,
    *,
    status: str,
    error: str = "",
    set_started: bool = False,
    set_finished: bool = False,
) -> None:
    run = _run_entry(tracker, run_id)
    if run is None:
        return
    phase = _phase_entry(run, phase_index)
    if phase is None:
        return
    phase["status"] = status
    if set_started:
        phase["started_at"] = _now_iso()
    if set_finished:
        phase["finished_at"] = _now_iso()
    if error:
        phase["error"] = error


def _set_action_status(
    tracker: dict[str, Any],
    run_id: str,
    phase_index: int,
    action_index: int,
    *,
    status: str,
    message: str = "",
    error: str = "",
    set_started: bool = False,
    set_finished: bool = False,
) -> None:
    run = _run_entry(tracker, run_id)
    if run is None:
        return
    phase = _phase_entry(run, phase_index)
    if phase is None:
        return
    action = _action_entry(phase, action_index)
    if action is None:
        return
    action["status"] = status
    if set_started:
        action["started_at"] = _now_iso()
    if set_finished:
        action["finished_at"] = _now_iso()
    if message:
        action["message"] = message
    if error:
        action["error"] = error


def _mark_pending_runs_skipped(tracker: dict[str, Any], reason: str) -> None:
    runs = tracker.get("runs")
    if not isinstance(runs, dict):
        return
    for run_entry in runs.values():
        if not isinstance(run_entry, dict):
            continue
        if str(run_entry.get("status") or "") != "PENDING":
            continue
        run_entry["status"] = "SKIPPED"
        run_entry["error"] = reason
        run_entry["finished_at"] = _now_iso()


def _run_has_explicit_connectivity_action(run_spec: dict[str, Any]) -> bool:
    for phase in _run_phases(run_spec):
        for action in _phase_actions(phase):
            if str(action.get("type") or "") == "check_connectivity":
                return True
    return False


def _precheck_target_nodes(
    *,
    run_spec: dict[str, Any],
    inventory_by_node: dict[str, dict[str, Any]],
) -> list[str]:
    targets: set[str] = set()
    for phase in _run_phases(run_spec):
        for action in _phase_actions(phase):
            target = str(action.get("target") or "").strip()
            if target:
                targets.add(target)
            action_targets = action.get("targets")
            if isinstance(action_targets, list):
                for item in action_targets:
                    item_text = str(item or "").strip()
                    if item_text:
                        targets.add(item_text)
    resolved = sorted(target for target in targets if target in inventory_by_node)
    return resolved if resolved else sorted(inventory_by_node.keys())


def _dispatch_next_pending_run(
    *,
    current_run: ProfilingRun,
    allow_dispatch: bool,
) -> dict[str, Any]:
    if not allow_dispatch:
        _task_trace(current_run.id, "next_run_dispatch_skipped")
        return {"queued": False, "reason": "dispatch_not_allowed"}

    try:
        with transaction.atomic():
            next_run = (
                ProfilingRun.objects.filter(
                    experiment=current_run.experiment,
                    requested_by=current_run.requested_by,
                    status=ProfilingRun.RunStatus.PENDING,
                    gen_version=current_run.gen_version,
                )
                .order_by("created_at", "id")
                .first()
            )
            if next_run is None:
                _task_trace(current_run.id, "next_run_not_found")
                return {"queued": False, "reason": "no_pending_run"}

            updated = ProfilingRun.objects.filter(
                id=next_run.id,
                status=ProfilingRun.RunStatus.PENDING,
            ).update(
                status=ProfilingRun.RunStatus.RUNNING,
                started_at=timezone.now(),
                finished_at=None,
                error_message="",
                task_id="",
                updated_at=timezone.now(),
            )
            if not updated:
                _task_trace(current_run.id, "next_run_claim_race", next_run_id=next_run.id)
                return {"queued": False, "reason": "claim_race", "next_run_id": next_run.id}
            next_run.refresh_from_db()
    except Exception as exc:
        _task_trace(current_run.id, "next_run_claim_failed", error=str(exc))
        return {"queued": False, "reason": "claim_failed", "error": str(exc)}

    try:
        task = execute_generated_plan_run_task.apply_async(
            kwargs={"generated_run_id": next_run.id},
            retry=False,
        )
    except Exception as exc:
        queue_error = str(exc)
        _task_trace(
            current_run.id,
            "next_run_queue_failed_sync_fallback",
            next_run_id=next_run.id,
            queue_error=queue_error,
        )
        result = execute_generated_plan_run_task.run(generated_run_id=next_run.id)
        next_run.refresh_from_db()
        return {
            "queued": False,
            "sync_fallback": True,
            "next_run_id": next_run.id,
            "queue_error": queue_error,
            "result_ok": bool(result.get("ok")) if isinstance(result, dict) else False,
        }

    next_run.task_id = task.id or ""
    next_run.save(update_fields=["task_id", "updated_at"])
    _task_trace(current_run.id, "next_run_queued", next_run_id=next_run.id, task_id=next_run.task_id)
    return {
        "queued": True,
        "sync_fallback": False,
        "next_run_id": next_run.id,
        "task_id": next_run.task_id,
    }


@shared_task(bind=True)
def execute_generated_plan_run_task(self, *, generated_run_id: int) -> dict[str, Any]:
    generated_run = ProfilingRun.objects.select_related(
        "experiment",
        "requested_by",
    ).get(id=generated_run_id)

    generated_run.status = ProfilingRun.RunStatus.RUNNING
    generated_run.started_at = timezone.now()
    generated_run.error_message = ""
    generated_run.metrics_collected = False
    existing_rrt_snapshot = (
        deepcopy(generated_run.rrt_config_execution_snapshot)
        if isinstance(generated_run.rrt_config_execution_snapshot, dict)
        else {}
    )
    if not isinstance(existing_rrt_snapshot.get("runs"), dict):
        existing_rrt_snapshot["runs"] = {}
    generated_run.rrt_config_execution_snapshot = existing_rrt_snapshot
    metrics_backend_base_path = generated_run.get_experiment_result_backend_path()
    metrics_remote_base_path = generated_run.get_experiment_result_target_host_path()
    generated_run.collected_metrics_path = metrics_backend_base_path
    generated_run.save(
        update_fields=[
            "status",
            "started_at",
            "error_message",
            "metrics_collected",
            "rrt_config_execution_snapshot",
            "collected_metrics_path",
            "updated_at",
        ]
    )

    experiment = generated_run.experiment
    experiment.sync_status_from_runs(save=True)
    payload = (
        generated_run.compiled_payload_snapshot
        if isinstance(generated_run.compiled_payload_snapshot, dict)
        else {}
    )
    if not payload:
        payload = experiment.generated_payload if isinstance(experiment.generated_payload, dict) else {}
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    execution_policy = payload.get("execution_policy") if isinstance(payload.get("execution_policy"), dict) else {}
    stop_on_failure = bool(execution_policy.get("stop_on_failure", generated_run.stop_on_failure))
    connectivity_precheck = bool(execution_policy.get("connectivity_precheck", True))
    tracker = _build_phase_action_tracker(runs)
    _append_tracker_event(
        tracker,
        "task_started",
        experiment_name=experiment.name,
        total_runs=len(runs),
        stop_on_failure=stop_on_failure,
        connectivity_precheck=connectivity_precheck,
        metrics_backend_base_path=metrics_backend_base_path,
        metrics_remote_base_path=metrics_remote_base_path,
    )
    _persist_tracker(generated_run, tracker)
    _task_trace(
        generated_run_id,
        "task_started",
        experiment_name=experiment.name,
        total_runs=len(runs),
        stop_on_failure=stop_on_failure,
        connectivity_precheck=connectivity_precheck,
        metrics_backend_base_path=metrics_backend_base_path,
        metrics_remote_base_path=metrics_remote_base_path,
    )

    source_snapshots = payload.get("source_snapshots") if isinstance(payload.get("source_snapshots"), dict) else {}
    scenario_snapshot = source_snapshots.get("scenario") if isinstance(source_snapshots.get("scenario"), dict) else {}
    experiment_snapshot = source_snapshots.get("experiment") if isinstance(source_snapshots.get("experiment"), dict) else {}

    inventory_by_node, inventory_errors = _build_inventory_by_node(generated_run.requested_by_id, scenario_snapshot)
    if inventory_errors:
        for run_id in list((tracker.get("runs") or {}).keys()):
            _set_run_status(
                tracker,
                run_id,
                status="FAILED",
                error="; ".join(inventory_errors),
                set_finished=True,
            )
        _append_tracker_event(tracker, "inventory_failed", errors=inventory_errors)
        _persist_tracker(generated_run, tracker)
        _task_trace(generated_run_id, "inventory_failed", errors=inventory_errors)

        generated_run.status = ProfilingRun.RunStatus.FAILED
        generated_run.finished_at = timezone.now()
        generated_run.failed_runs = len(runs) if runs else 1
        generated_run.error_message = "; ".join(inventory_errors)
        generated_run.results = []
        generated_run.save(
            update_fields=[
                "status",
                "finished_at",
                "failed_runs",
                "error_message",
                "results",
                "updated_at",
            ]
        )
        experiment.sync_status_from_runs(save=True)
        next_run_dispatch = _dispatch_next_pending_run(
            current_run=generated_run,
            allow_dispatch=not stop_on_failure,
        )
        return {
            "ok": False,
            "error": generated_run.error_message,
            "results": [],
            "stop_on_failure": stop_on_failure,
            "next_run_dispatch": next_run_dispatch,
        }
    _append_tracker_event(tracker, "inventory_resolved", nodes=sorted(inventory_by_node.keys()))
    _persist_tracker(generated_run, tracker)
    _task_trace(generated_run_id, "inventory_resolved", nodes=sorted(inventory_by_node.keys()))

    run_results: list[dict[str, Any]] = []
    overall_ok = True
    completed_runs = 0
    failed_runs = 0
    metrics_collection_attempted = False
    metrics_collection_success = True
    rrt_snapshots = generated_run.rrt_config_execution_snapshot if isinstance(
        generated_run.rrt_config_execution_snapshot, dict
    ) else {"runs": {}}
    if not isinstance(rrt_snapshots.get("runs"), dict):
        rrt_snapshots["runs"] = {}
    runtime_verified_nodes: set[str] = set()

    for run_index, run_spec in enumerate(runs, start=1):
        if not isinstance(run_spec, dict):
            failed_runs += 1
            overall_ok = False
            _append_tracker_event(tracker, "run_invalid_spec", run_index=run_index)
            if stop_on_failure:
                break
            continue

        run_id = str(run_spec.get("run_id") or f"run-{run_index:03d}")
        _set_run_status(tracker, run_id, status="RUNNING", set_started=True)
        _append_tracker_event(tracker, "run_started", run_id=run_id, run_index=run_index)
        _persist_tracker(generated_run, tracker)
        _task_trace(generated_run_id, "run_started", run_id=run_id, run_index=run_index)

        runtime_env_name = str(run_spec.get("runtime_env_name") or "").strip()
        run_metrics_backend_dir = _join_path(metrics_backend_base_path, run_id)
        run_metrics_remote_dir = _join_path(metrics_remote_base_path, run_id)
        _append_tracker_event(
            tracker,
            "run_metrics_paths_resolved",
            run_id=run_id,
            metrics_backend_dir=run_metrics_backend_dir,
            metrics_remote_dir=run_metrics_remote_dir,
        )
        _persist_tracker(generated_run, tracker)
        _task_trace(
            generated_run_id,
            "run_metrics_paths_resolved",
            run_id=run_id,
            metrics_backend_dir=run_metrics_backend_dir,
            metrics_remote_dir=run_metrics_remote_dir,
        )

        runtime_env_payload, runtime_error = _resolve_runtime_env_payload(generated_run.requested_by_id, runtime_env_name)
        if runtime_error:
            run_results.append(
                {
                    "run_id": run_id,
                    "runtime_env_name": runtime_env_name,
                    "status": "FAILED",
                    "error": runtime_error,
                    "metrics_backend_dir": run_metrics_backend_dir,
                    "metrics_remote_dir": run_metrics_remote_dir,
                    "phases": [],
                }
            )
            failed_runs += 1
            overall_ok = False
            _set_run_status(
                tracker,
                run_id,
                status="FAILED",
                error=runtime_error,
                set_finished=True,
            )
            _append_tracker_event(
                tracker,
                "run_failed_runtime_env",
                run_id=run_id,
                runtime_env_name=runtime_env_name,
                error=runtime_error,
            )
            _persist_tracker(generated_run, tracker)
            _task_trace(
                generated_run_id,
                "run_failed_runtime_env",
                run_id=run_id,
                runtime_env_name=runtime_env_name,
                error=runtime_error,
            )
            if stop_on_failure:
                break
            continue

        if connectivity_precheck and not _run_has_explicit_connectivity_action(run_spec):
            precheck_targets = _precheck_target_nodes(
                run_spec=run_spec,
                inventory_by_node=inventory_by_node,
            )
            _append_tracker_event(
                tracker,
                "run_precheck_started",
                run_id=run_id,
                targets=precheck_targets,
            )
            _persist_tracker(generated_run, tracker)
            _task_trace(
                generated_run_id,
                "run_precheck_started",
                run_id=run_id,
                targets=precheck_targets,
            )
            precheck_errors: list[str] = []
            for target_node in precheck_targets:
                precheck_action = _with_cached_connectivity_runtime_flag(
                    action={"type": "check_connectivity", "target": target_node},
                    runtime_verified_nodes=runtime_verified_nodes,
                )
                try:
                    precheck_result = _execute_one_action(
                        inventory_by_node=inventory_by_node,
                        runtime_env_payload=runtime_env_payload,
                        scenario_snapshot=scenario_snapshot,
                        experiment_snapshot=experiment_snapshot,
                        phase={"name": "__precheck_connectivity__"},
                        action=precheck_action,
                    )
                except ActionExecutionError as exc:
                    precheck_result = {
                        "type": "check_connectivity",
                        "target": target_node,
                        "status": "FAILED",
                        "success": False,
                        "message": str(exc),
                        "debug": {},
                    }
                _sync_connectivity_result_to_inventory(
                    user_id=generated_run.requested_by_id,
                    run_id=run_id,
                    action=precheck_action,
                    action_result=precheck_result,
                    inventory_by_node=inventory_by_node,
                    runtime_verified_nodes=runtime_verified_nodes,
                )
                if not precheck_result.get("success"):
                    precheck_errors.append(
                        f"{target_node}: {str(precheck_result.get('message') or 'connectivity check failed')}"
                    )
            if precheck_errors:
                run_results.append(
                    {
                        "run_id": run_id,
                        "runtime_env_name": runtime_env_name,
                        "status": "FAILED",
                        "error": "Connectivity precheck failed: " + "; ".join(precheck_errors),
                        "variant_values": run_spec.get("variant_values")
                        if isinstance(run_spec.get("variant_values"), dict)
                        else {},
                        "metrics_backend_dir": run_metrics_backend_dir,
                        "metrics_remote_dir": run_metrics_remote_dir,
                        "phases": [],
                    }
                )
                failed_runs += 1
                overall_ok = False
                _set_run_status(
                    tracker,
                    run_id,
                    status="FAILED",
                    error="Connectivity precheck failed: " + "; ".join(precheck_errors),
                    set_finished=True,
                )
                _append_tracker_event(
                    tracker,
                    "run_precheck_finished",
                    run_id=run_id,
                    ok=False,
                    errors=precheck_errors,
                )
                _persist_tracker(generated_run, tracker)
                _task_trace(
                    generated_run_id,
                    "run_precheck_finished",
                    run_id=run_id,
                    ok=False,
                    errors=precheck_errors,
                )
                if stop_on_failure:
                    _mark_pending_runs_skipped(
                        tracker,
                        reason="Skipped due to stop_on_failure after earlier run failure.",
                    )
                    _append_tracker_event(
                        tracker,
                        "stop_on_failure_triggered",
                        run_id=run_id,
                    )
                    _persist_tracker(generated_run, tracker)
                    _task_trace(generated_run_id, "stop_on_failure_triggered", run_id=run_id)
                    break
                continue
            _append_tracker_event(
                tracker,
                "run_precheck_finished",
                run_id=run_id,
                ok=True,
                targets=precheck_targets,
            )
            _persist_tracker(generated_run, tracker)
            _task_trace(
                generated_run_id,
                "run_precheck_finished",
                run_id=run_id,
                ok=True,
                targets=precheck_targets,
            )

        run_variant_values = run_spec.get("variant_values") if isinstance(run_spec.get("variant_values"), dict) else {}
        run_auto_rrt_overrides = _variant_values_to_rrt_overrides(run_variant_values)
        existing_run_snapshot = (
            rrt_snapshots["runs"].get(run_id)
            if isinstance(rrt_snapshots["runs"].get(run_id), dict)
            else {}
        )
        run_snapshot = {
            "runtime_env_name": runtime_env_name,
            "variant_values": run_variant_values,
            "auto_rrt_overrides": run_auto_rrt_overrides,
            "runtime_rrt_config_path": str(runtime_env_payload.get("serializer_rrt_config_path") or ""),
            "runtime_has_rrt_config": bool(str(runtime_env_payload.get("serializer_rrt_config_raw_yaml") or "").strip()),
            "actions": existing_run_snapshot.get("actions") if isinstance(existing_run_snapshot.get("actions"), list) else [],
        }
        if isinstance(existing_run_snapshot, dict):
            run_snapshot = {**existing_run_snapshot, **run_snapshot}
        rrt_snapshots["runs"][run_id] = run_snapshot
        generated_run.rrt_config_execution_snapshot = rrt_snapshots
        generated_run.save(update_fields=["rrt_config_execution_snapshot", "updated_at"])

        run_ok = True
        phase_results: list[dict[str, Any]] = []
        for phase_index, phase in enumerate(_run_phases(run_spec), start=1):
            phase_name = str(phase.get("name") or "")
            action_results: list[dict[str, Any]] = []

            _set_phase_status(tracker, run_id, phase_index, status="RUNNING", set_started=True)
            _append_tracker_event(
                tracker,
                "phase_started",
                run_id=run_id,
                phase_name=phase_name,
                phase_index=phase_index,
            )
            _persist_tracker(generated_run, tracker)
            _task_trace(
                generated_run_id,
                "phase_started",
                run_id=run_id,
                phase_name=phase_name,
                phase_index=phase_index,
            )

            for action_index, action in enumerate(_phase_actions(phase), start=1):
                prepared_action = _prepare_action_payload_for_execution(
                    action=action,
                    inventory_by_node=inventory_by_node,
                    run_metrics_remote_dir=run_metrics_remote_dir,
                    run_metrics_backend_dir=run_metrics_backend_dir,
                    run_variant_values=(
                        run_spec.get("variant_values")
                        if isinstance(run_spec.get("variant_values"), dict)
                        else None
                    ),
                    run_rrt_config_yaml=str(run_snapshot.get("generated_rrt_config_yaml") or ""),
                )
                prepared_action = _with_cached_connectivity_runtime_flag(
                    action=prepared_action,
                    runtime_verified_nodes=runtime_verified_nodes,
                )
                action_type = str(prepared_action.get("type") or "")
                action_target = str(prepared_action.get("target") or "")
                action_preset = str(prepared_action.get("preset") or "")
                action_params = prepared_action.get("parameters")
                action_metrics_dir = ""
                if isinstance(action_params, dict):
                    action_metrics_dir = str(
                        action_params.get("icopa_metrics_dir")
                        or action_params.get("validate_metrics_dir")
                        or action_params.get("validate_metrics_path")
                        or ""
                    )
                if action_type == "run_runtime_preset" and action_preset.startswith("profiling/"):
                    action_rrt_overrides = action_params.get("rrt_config_overrides") if isinstance(action_params, dict) else {}
                    if not isinstance(action_rrt_overrides, dict):
                        action_rrt_overrides = {}
                    provided_rrt_yaml = str(action_params.get("rrt_config_yaml") or "") if isinstance(action_params, dict) else ""
                    effective_rrt_yaml = provided_rrt_yaml or _render_effective_rrt_config_yaml(
                        runtime_rrt_yaml=str(runtime_env_payload.get("serializer_rrt_config_raw_yaml") or ""),
                        overrides=action_rrt_overrides,
                    )
                    rrt_file_path = str(action_params.get("rrt_config_file_path") or "") if isinstance(action_params, dict) else ""
                    rrt_yaml_sha256 = (
                        hashlib.sha256(effective_rrt_yaml.encode("utf-8")).hexdigest()
                        if effective_rrt_yaml
                        else ""
                    )
                    run_snapshot_actions = run_snapshot.get("actions") if isinstance(run_snapshot.get("actions"), list) else []
                    run_snapshot_actions.append(
                        {
                            "phase_name": phase_name,
                            "phase_index": phase_index,
                            "action_index": action_index,
                            "preset": action_preset,
                            "target": action_target,
                            "metrics_dir": action_metrics_dir,
                            "rrt_config_overrides": action_rrt_overrides,
                            "rrt_config_file_path": rrt_file_path,
                            "rrt_config_yaml": effective_rrt_yaml,
                            "rrt_config_yaml_b64": (
                                base64.b64encode(effective_rrt_yaml.encode("utf-8")).decode("ascii")
                                if effective_rrt_yaml
                                else ""
                            ),
                            "rrt_config_yaml_sha256": rrt_yaml_sha256,
                        }
                    )
                    run_snapshot["actions"] = run_snapshot_actions
                    generated_run.rrt_config_execution_snapshot = rrt_snapshots
                    generated_run.save(update_fields=["rrt_config_execution_snapshot", "updated_at"])
                    _append_tracker_event(
                        tracker,
                        "profiling_rrt_config_resolved",
                        run_id=run_id,
                        phase_name=phase_name,
                        phase_index=phase_index,
                        action_index=action_index,
                        preset=action_preset,
                        target=action_target,
                        rrt_config_file_path=rrt_file_path,
                        rrt_config_yaml_size=len(effective_rrt_yaml),
                        rrt_config_yaml_sha256=rrt_yaml_sha256,
                        from_precomputed_snapshot=bool(provided_rrt_yaml),
                    )
                    _persist_tracker(generated_run, tracker)
                    _task_trace(
                        generated_run_id,
                        "profiling_rrt_config_resolved",
                        run_id=run_id,
                        phase_name=phase_name,
                        phase_index=phase_index,
                        action_index=action_index,
                        preset=action_preset,
                        target=action_target,
                        rrt_config_file_path=rrt_file_path,
                        rrt_config_yaml_size=len(effective_rrt_yaml),
                        rrt_config_yaml_sha256=rrt_yaml_sha256[:12],
                        from_precomputed_snapshot=bool(provided_rrt_yaml),
                    )
                _set_action_status(
                    tracker,
                    run_id,
                    phase_index,
                    action_index,
                    status="RUNNING",
                    set_started=True,
                )
                _append_tracker_event(
                    tracker,
                    "action_started",
                    run_id=run_id,
                    phase_name=phase_name,
                    phase_index=phase_index,
                    action_index=action_index,
                    action_type=action_type,
                    target=action_target,
                    preset=action_preset,
                    metrics_dir=action_metrics_dir,
                )
                _persist_tracker(generated_run, tracker)
                _task_trace(
                    generated_run_id,
                    "action_started",
                    run_id=run_id,
                    phase_name=phase_name,
                    phase_index=phase_index,
                    action_index=action_index,
                    action_type=action_type,
                    target=action_target,
                    preset=action_preset,
                    metrics_dir=action_metrics_dir,
                )

                try:
                    action_result = _execute_one_action(
                        inventory_by_node=inventory_by_node,
                        runtime_env_payload=runtime_env_payload,
                        scenario_snapshot=scenario_snapshot,
                        experiment_snapshot=experiment_snapshot,
                        phase=phase,
                        action=prepared_action,
                    )
                except ActionExecutionError as exc:
                    action_result = {
                        "type": action_type,
                        "target": action_target,
                        "status": "FAILED",
                        "success": False,
                        "message": str(exc),
                        "debug": {},
                    }
                _sync_connectivity_result_to_inventory(
                    user_id=generated_run.requested_by_id,
                    run_id=run_id,
                    action=prepared_action,
                    action_result=action_result,
                    inventory_by_node=inventory_by_node,
                    runtime_verified_nodes=runtime_verified_nodes,
                )
                action_results.append(action_result)
                if action_type == "collect_metrics":
                    metrics_collection_attempted = True
                    metrics_collection_success = metrics_collection_success and bool(action_result.get("success"))
                if action_result.get("success"):
                    _set_action_status(
                        tracker,
                        run_id,
                        phase_index,
                        action_index,
                        status="SUCCEEDED",
                        message=str(action_result.get("message") or ""),
                        set_finished=True,
                    )
                    _append_tracker_event(
                        tracker,
                        "action_succeeded",
                        run_id=run_id,
                        phase_name=phase_name,
                        phase_index=phase_index,
                        action_index=action_index,
                        action_type=action_type,
                        target=action_target,
                        preset=action_preset,
                        metrics_dir=action_metrics_dir,
                    )
                    _persist_tracker(generated_run, tracker)
                    _task_trace(
                        generated_run_id,
                        "action_succeeded",
                        run_id=run_id,
                        phase_name=phase_name,
                        phase_index=phase_index,
                        action_index=action_index,
                        action_type=action_type,
                        target=action_target,
                        preset=action_preset,
                        metrics_dir=action_metrics_dir,
                    )
                if not action_result.get("success"):
                    run_ok = False
                    failure_message = str(action_result.get("message") or "action failed")
                    _set_action_status(
                        tracker,
                        run_id,
                        phase_index,
                        action_index,
                        status="FAILED",
                        error=failure_message,
                        set_finished=True,
                    )
                    _append_tracker_event(
                        tracker,
                        "action_failed",
                        run_id=run_id,
                        phase_name=phase_name,
                        phase_index=phase_index,
                        action_index=action_index,
                        action_type=action_type,
                        target=action_target,
                        preset=action_preset,
                        metrics_dir=action_metrics_dir,
                        error=failure_message,
                    )
                    _persist_tracker(generated_run, tracker)
                    _task_trace(
                        generated_run_id,
                        "action_failed",
                        run_id=run_id,
                        phase_name=phase_name,
                        phase_index=phase_index,
                        action_index=action_index,
                        action_type=action_type,
                        target=action_target,
                        preset=action_preset,
                        metrics_dir=action_metrics_dir,
                        error=failure_message,
                    )
                    break
            phase_failed_message = ""
            if not run_ok:
                for item in action_results:
                    if isinstance(item, dict) and not item.get("success"):
                        phase_failed_message = str(item.get("message") or "phase action failed")
                        break
                _set_phase_status(
                    tracker,
                    run_id,
                    phase_index,
                    status="FAILED",
                    error=phase_failed_message,
                    set_finished=True,
                )
            else:
                _set_phase_status(
                    tracker,
                    run_id,
                    phase_index,
                    status="SUCCEEDED",
                    set_finished=True,
                )
            _append_tracker_event(
                tracker,
                "phase_finished",
                run_id=run_id,
                phase_name=phase_name,
                phase_index=phase_index,
                status="FAILED" if not run_ok else "SUCCEEDED",
                error=phase_failed_message,
            )
            _persist_tracker(generated_run, tracker)
            _task_trace(
                generated_run_id,
                "phase_finished",
                run_id=run_id,
                phase_name=phase_name,
                phase_index=phase_index,
                status="FAILED" if not run_ok else "SUCCEEDED",
                error=phase_failed_message,
            )
            phase_results.append(
                {
                    "name": phase_name,
                    "mode": str(phase.get("mode") or "sequential"),
                    "actions": action_results,
                    "status": "SUCCEEDED" if all(item.get("success") for item in action_results) else "FAILED",
                }
            )
            if not run_ok:
                break

        if run_ok:
            completed_runs += 1
            run_results.append(
                {
                    "run_id": run_id,
                    "runtime_env_name": runtime_env_name,
                    "status": "SUCCEEDED",
                    "variant_values": run_spec.get("variant_values") if isinstance(run_spec.get("variant_values"), dict) else {},
                    "metrics_backend_dir": run_metrics_backend_dir,
                    "metrics_remote_dir": run_metrics_remote_dir,
                    "phases": phase_results,
                }
            )
            _set_run_status(tracker, run_id, status="SUCCEEDED", set_finished=True)
            _append_tracker_event(tracker, "run_finished", run_id=run_id, status="SUCCEEDED")
            _persist_tracker(generated_run, tracker)
            _task_trace(generated_run_id, "run_finished", run_id=run_id, status="SUCCEEDED")
        else:
            failed_runs += 1
            overall_ok = False
            run_results.append(
                {
                    "run_id": run_id,
                    "runtime_env_name": runtime_env_name,
                    "status": "FAILED",
                    "variant_values": run_spec.get("variant_values") if isinstance(run_spec.get("variant_values"), dict) else {},
                    "metrics_backend_dir": run_metrics_backend_dir,
                    "metrics_remote_dir": run_metrics_remote_dir,
                    "phases": phase_results,
                }
            )
            _set_run_status(
                tracker,
                run_id,
                status="FAILED",
                error="One or more actions failed.",
                set_finished=True,
            )
            _append_tracker_event(tracker, "run_finished", run_id=run_id, status="FAILED")
            _persist_tracker(generated_run, tracker)
            _task_trace(generated_run_id, "run_finished", run_id=run_id, status="FAILED")
            if stop_on_failure:
                _mark_pending_runs_skipped(
                    tracker,
                    reason="Skipped due to stop_on_failure after earlier run failure.",
                )
                _append_tracker_event(
                    tracker,
                    "stop_on_failure_triggered",
                    run_id=run_id,
                )
                _persist_tracker(generated_run, tracker)
                _task_trace(generated_run_id, "stop_on_failure_triggered", run_id=run_id)
                break

    _append_tracker_event(
        tracker,
        "task_finished",
        ok=overall_ok,
        completed_runs=completed_runs,
        failed_runs=failed_runs,
    )
    _persist_tracker(generated_run, tracker)
    _task_trace(
        generated_run_id,
        "task_finished",
        ok=overall_ok,
        completed_runs=completed_runs,
        failed_runs=failed_runs,
    )

    generated_run.completed_runs = completed_runs
    generated_run.failed_runs = failed_runs
    generated_run.results = run_results
    generated_run.finished_at = timezone.now()
    generated_run.status = ProfilingRun.RunStatus.SUCCEEDED if overall_ok else ProfilingRun.RunStatus.FAILED
    generated_run.error_message = "" if overall_ok else "One or more generated runs failed."
    generated_run.metrics_collected = bool(metrics_collection_attempted and metrics_collection_success)
    generated_run.rrt_config_execution_snapshot = rrt_snapshots
    generated_run.save(
        update_fields=[
            "completed_runs",
            "failed_runs",
            "results",
            "finished_at",
            "status",
            "error_message",
            "metrics_collected",
            "rrt_config_execution_snapshot",
            "collected_metrics_path",
            "phase_action_execution_status",
            "updated_at",
        ]
    )
    experiment.sync_status_from_runs(save=True)
    next_run_dispatch = _dispatch_next_pending_run(
        current_run=generated_run,
        allow_dispatch=bool(overall_ok or not stop_on_failure),
    )

    return {
        "ok": overall_ok,
        "stop_on_failure": stop_on_failure,
        "connectivity_precheck": connectivity_precheck,
        "completed_runs": completed_runs,
        "failed_runs": failed_runs,
        "results": run_results,
        "next_run_dispatch": next_run_dispatch,
    }

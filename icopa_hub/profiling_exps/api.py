"""APIs for profiling experiment definitions."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
import yaml

from icopa_core.task_executor.exec_pipeline_gen import (
    ExecPipelineCompileError,
    compile_experiment_plan,
    render_generated_plan_yaml,
)
from icopa_core.task_executor import ActionExecutionError, ExecutionContext, build_executor
from runtime_env.models import RuntimeEnvironment
from scenarios.models import Scenario

from .celery_tasks import (
    _build_inventory_by_node,
    _resolve_runtime_env_payload,
    _variant_values_to_rrt_overrides,
    execute_generated_plan_run_task,
)
from .models import (
    ProfilingComparision,
    ProfilingComparisionRun,
    ProfilingExperiment,
    ProfilingRun,
)
from .serializers import (
    ProfilingComparisionCreateSerializer,
    ProfilingComparisionSerializer,
    ProfilingExperimentSerializer,
    ProfilingExperimentUploadSerializer,
    ProfilingRunSerializer,
)
from .services.artifacts import delete_run_artifacts


def _collect_field_updates(obj, new_values: dict) -> dict:
    changed_fields = {}
    for field_name, new_value in new_values.items():
        old_value = getattr(obj, field_name)
        if old_value != new_value:
            changed_fields[field_name] = {"old": old_value, "new": new_value}
    return changed_fields


def _purge_stale_pending_runs(*, experiment: ProfilingExperiment) -> int:
    stale_statuses = {
        ProfilingRun.RunStatus.PENDING,
    }
    queryset = ProfilingRun.objects.filter(experiment=experiment, status__in=stale_statuses)
    deleted_count, _ = queryset.delete()
    return int(deleted_count or 0)


def _create_pending_run(
    *,
    experiment: ProfilingExperiment,
    requested_by_id: int,
    gen_version: int,
    plan_run_id: str,
    compiled_payload_snapshot: dict[str, Any],
    run_metadata: dict[str, Any] | None,
    characterization_parameters: dict[str, Any] | None,
    total_runs: int,
    stop_on_failure: bool,
    rrt_config_execution_snapshot: dict[str, Any] | None = None,
) -> ProfilingRun:
    return ProfilingRun.objects.create(
        experiment=experiment,
        requested_by_id=requested_by_id,
        status=ProfilingRun.RunStatus.PENDING,
        gen_version=max(1, int(gen_version or 1)),
        plan_run_id=str(plan_run_id or "").strip(),
        stop_on_failure=bool(stop_on_failure),
        total_runs=max(0, int(total_runs or 0)),
        completed_runs=0,
        failed_runs=0,
        run_metadata=deepcopy(run_metadata) if isinstance(run_metadata, dict) else {},
        characterization_parameters=(
            deepcopy(characterization_parameters)
            if isinstance(characterization_parameters, dict)
            else {}
        ),
        compiled_payload_snapshot=compiled_payload_snapshot if isinstance(compiled_payload_snapshot, dict) else {},
        results=[],
        phase_action_execution_status={},
        rrt_config_execution_snapshot=(
            deepcopy(rrt_config_execution_snapshot)
            if isinstance(rrt_config_execution_snapshot, dict)
            else {}
        ),
        metrics_collected=False,
        collected_metrics_path="",
        task_id="",
        error_message="",
        started_at=None,
        finished_at=None,
    )


def _lookup_variant_value(
    variant_values: dict[str, Any],
    *,
    preferred_keys: tuple[str, ...],
) -> tuple[Any, str]:
    for key in preferred_keys:
        if key not in variant_values:
            continue
        value = variant_values.get(key)
        if value in (None, ""):
            continue
        return value, key

    for raw_key, raw_value in variant_values.items():
        key = str(raw_key or "").strip()
        if not key or raw_value in (None, ""):
            continue
        suffix = key.split(".")[-1]
        if suffix in preferred_keys:
            return raw_value, key
    return None, ""


def _build_run_metadata(*, run_spec: dict[str, Any], plan_run_id: str) -> dict[str, Any]:
    variant_values = run_spec.get("variant_values") if isinstance(run_spec.get("variant_values"), dict) else {}
    edge = run_spec.get("edge") if isinstance(run_spec.get("edge"), dict) else {}

    payload_value, payload_key = _lookup_variant_value(
        variant_values,
        preferred_keys=("payload_size", "payload", "payload_bytes", "message_size", "msg_size"),
    )
    response_payload_value, response_payload_key = _lookup_variant_value(
        variant_values,
        preferred_keys=("response_payload_size", "response_payload", "response_payload_bytes"),
    )
    publish_rate_value, publish_rate_key = _lookup_variant_value(
        variant_values,
        preferred_keys=(
            "frequency_hz",
            "publish_rate_hz",
            "publish_rate",
            "publish_frequency_hz",
            "publish_frequency",
            "frequency",
            "pub_rate_hz",
        ),
    )

    return {
        "plan_run_id": str(plan_run_id or "").strip(),
        "runtime_env_name": str(run_spec.get("runtime_env_name") or "").strip(),
        "variant_values": deepcopy(variant_values),
        "edge": deepcopy(edge),
        "payload_key": payload_key,
        "payload_value": payload_value,
        "response_payload_key": response_payload_key,
        "response_payload_value": response_payload_value,
        "publish_rate_key": publish_rate_key,
        "publish_rate_value": publish_rate_value,
    }


def _single_run_payload_snapshot(
    *,
    compiled_payload: dict[str, Any],
    run_spec: dict[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(compiled_payload)
    payload["runs"] = [deepcopy(run_spec)]
    payload["total_generated_runs"] = 1
    return payload


def _extract_experiment_payload(payload: dict) -> tuple[str, str, str, str, dict, dict, list]:
    if not isinstance(payload, dict):
        raise ValidationError({"file": "Experiment YAML root must be an object."})

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValidationError({"metadata": "metadata must be an object when provided."})

    spec = payload.get("spec") or {}
    if not isinstance(spec, dict):
        raise ValidationError({"spec": "spec must be an object."})

    name = metadata.get("name") or payload.get("name")
    if not name:
        raise ValidationError({"metadata": "metadata.name is required."})

    kind = payload.get("kind") or "ExperimentPlan"
    description = metadata.get("description") or payload.get("description") or ""
    scenario_name = metadata.get("scenarioRef") or spec.get("scenarioRef")
    if not scenario_name:
        raise ValidationError({"metadata": "metadata.scenarioRef (or spec.scenarioRef) is required."})

    execution = spec.get("execution") or {}
    selections = spec.get("selections") or {}
    phases = spec.get("phases")
    exec_edge = spec.get("execEdge")
    if phases is None and exec_edge is None and any(
        spec.get(field_name) is not None
        for field_name in ("phasesPrerun", "phasesInEachRun", "phases_pre_run", "phases_in_each_run")
    ):
        raise ValidationError(
            {"phases": "spec.phasesPrerun/phasesInEachRun are not supported. Use spec.phases or spec.execEdge."}
        )
    if phases is None:
        phases = []
    if exec_edge is None:
        exec_edge = []

    if not isinstance(execution, dict):
        raise ValidationError({"execution": "spec.execution must be an object."})
    if not isinstance(selections, dict):
        raise ValidationError({"selections": "spec.selections must be an object."})
    if not isinstance(phases, list):
        raise ValidationError({"phases": "spec.phases must be a list."})
    if not isinstance(exec_edge, list):
        raise ValidationError({"execEdge": "spec.execEdge must be a list."})
    if not phases and not exec_edge:
        raise ValidationError({"phases": "spec.phases or spec.execEdge must be non-empty."})

    if not phases and exec_edge:
        flattened_phases: list[dict[str, Any]] = []
        for block_idx, block in enumerate(exec_edge):
            if not isinstance(block, dict):
                continue
            block_edge_refs = block.get("edgeRefs")
            block_phases = block.get("phases")
            if not isinstance(block_phases, list):
                continue
            for phase_idx, raw_phase in enumerate(block_phases):
                if not isinstance(raw_phase, dict):
                    continue
                phase_payload = deepcopy(raw_phase)
                if "edgeRefs" not in phase_payload and isinstance(block_edge_refs, list):
                    phase_payload["edgeRefs"] = deepcopy(block_edge_refs)
                if "name" not in phase_payload:
                    phase_payload["name"] = f"execEdge-{block_idx + 1}-phase-{phase_idx + 1}"
                flattened_phases.append(phase_payload)
        phases = flattened_phases

    return name, kind, description, scenario_name, execution, selections, phases


def _stable_sha256(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_phase_action(action: dict) -> str:
    action_type = str(action.get("type") or action.get("action") or "unknown")
    if action_type == "run_runtime_preset":
        preset = str(action.get("preset") or "-")
        return f"{action_type}:{preset}@{action.get('target', '-')}"
    if action_type == "wait":
        params = action.get("parameters")
        seconds = params.get("seconds", 0) if isinstance(params, dict) else 0
        return f"wait({seconds}s)"
    if action_type == "collect_metrics":
        return "collect_metrics"
    target = action.get("target")
    return f"{action_type}@{target}" if target else action_type


def _phase_summaries(compiled_payload: dict) -> list[dict]:
    runs = compiled_payload.get("runs") if isinstance(compiled_payload.get("runs"), list) else []
    summaries: list[dict] = []
    for run in runs:
        phases = run.get("phases") if isinstance(run, dict) else []
        if not isinstance(phases, list):
            continue
        run_phase_summary = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
            run_phase_summary.append(
                {
                    "name": str(phase.get("name") or "unnamed-phase"),
                    "actions": [_render_phase_action(item) for item in actions if isinstance(item, dict)],
                }
            )
        summaries.append(
            {
                "run_id": str(run.get("run_id") or ""),
                "variant_values": run.get("variant_values") if isinstance(run.get("variant_values"), dict) else {},
                "edge": run.get("edge") if isinstance(run.get("edge"), dict) else {},
                "phases": run_phase_summary,
            }
        )
    return summaries


def _dict_deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _dict_deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _flatten_leaf_paths(payload: Any, prefix: str = "") -> list[str]:
    if isinstance(payload, dict):
        out: list[str] = []
        for key, value in payload.items():
            key_str = str(key)
            next_prefix = f"{prefix}.{key_str}" if prefix else key_str
            out.extend(_flatten_leaf_paths(value, next_prefix))
        return out
    return [prefix] if prefix else []


def _has_path(payload: dict[str, Any], path: str) -> bool:
    node: Any = payload
    for segment in [part for part in str(path or "").split(".") if part]:
        if not isinstance(node, dict) or segment not in node:
            return False
        node = node.get(segment)
    return True


def _collect_rrt_variable_checks(
    *,
    user_id: int,
    compiled_payload: dict[str, Any],
) -> dict[str, Any]:
    runs = compiled_payload.get("runs") if isinstance(compiled_payload.get("runs"), list) else []
    runtime_names = {
        str(run.get("runtime_env_name") or "").strip()
        for run in runs
        if isinstance(run, dict) and str(run.get("runtime_env_name") or "").strip()
    }
    runtime_by_name = {
        env.name: env
        for env in RuntimeEnvironment.objects.filter(created_by_id=user_id, name__in=sorted(runtime_names))
    }

    report_runs: list[dict[str, Any]] = []
    failed_runs: list[str] = []
    for index, run in enumerate(runs, start=1):
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or f"run-{index:03d}")
        runtime_env_name = str(run.get("runtime_env_name") or "").strip()
        variant_values = run.get("variant_values") if isinstance(run.get("variant_values"), dict) else {}
        auto_overrides = _variant_values_to_rrt_overrides(variant_values)
        report_item = {
            "run_id": run_id,
            "runtime_env_name": runtime_env_name,
            "variant_values": variant_values,
            "auto_rrt_overrides": auto_overrides,
            "status": "NO_OVERRIDES",
            "missing_paths": [],
            "messages": [],
        }
        if not auto_overrides:
            report_runs.append(report_item)
            continue

        runtime_env = runtime_by_name.get(runtime_env_name)
        if runtime_env is None:
            report_item["status"] = "SKIPPED_RUNTIME_ENV_NOT_FOUND"
            report_item["messages"].append(f"Runtime environment '{runtime_env_name}' not found at generation-time.")
            report_runs.append(report_item)
            continue
        raw_rrt = str(runtime_env.serializer_rrt_config_raw_yaml or "").strip()
        if not raw_rrt:
            report_item["status"] = "SKIPPED_RRT_CONFIG_EMPTY"
            report_item["messages"].append(
                f"Runtime environment '{runtime_env_name}' has no stored serializer_rrt_config.yaml."
            )
            report_runs.append(report_item)
            continue

        try:
            base_payload = yaml.safe_load(raw_rrt) or {}
        except yaml.YAMLError as exc:
            report_item["status"] = "FAILED"
            report_item["messages"].append(f"Invalid runtime serializer_rrt_config YAML: {exc}")
            report_runs.append(report_item)
            failed_runs.append(run_id)
            continue
        if not isinstance(base_payload, dict):
            report_item["status"] = "FAILED"
            report_item["messages"].append("Runtime serializer_rrt_config root must be an object.")
            report_runs.append(report_item)
            failed_runs.append(run_id)
            continue

        missing_paths = [
            path
            for path in _flatten_leaf_paths(auto_overrides)
            if not _has_path(base_payload, path)
        ]
        if missing_paths:
            report_item["status"] = "FAILED"
            report_item["missing_paths"] = missing_paths
            report_item["messages"].append(
                "One or more experiment variant variables do not match serializer_rrt_config.yaml keys."
            )
            report_runs.append(report_item)
            failed_runs.append(run_id)
            continue

        report_item["status"] = "OK"
        report_item["preview_merged_rrt_config"] = _dict_deep_merge(base_payload, auto_overrides)
        report_runs.append(report_item)

    return {
        "ok": len(failed_runs) == 0,
        "failed_runs": failed_runs,
        "runs": report_runs,
    }


def _write_generated_rrt_config_preview_files(*, generated_run: ProfilingRun) -> int:
    snapshot = (
        generated_run.rrt_config_execution_snapshot
        if isinstance(generated_run.rrt_config_execution_snapshot, dict)
        else {}
    )
    runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), dict) else {}
    if not runs:
        return 0

    base_dir = Path(settings.BASE_DIR).resolve()
    updated = False
    written_count = 0
    for run_id, run_item_raw in runs.items():
        if not isinstance(run_item_raw, dict):
            continue
        run_item = run_item_raw
        run_id_text = str(run_id or "").strip()
        yaml_text = str(run_item.get("generated_rrt_config_yaml") or "").strip()
        if not run_id_text or not yaml_text:
            continue

        rel_path = (
            f"{generated_run.get_experiment_result_backend_path().rstrip('/')}/"
            f"{run_id_text}/vm_home/rrt_config.yaml"
        )
        local_path = (base_dir / rel_path.lstrip("/")).resolve()
        if not local_path.is_relative_to(base_dir):
            raise ValueError(f"Unsafe generated preview path: {local_path}")

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(yaml_text, encoding="utf-8")
        run_item["generated_rrt_config_backend_path"] = rel_path
        run_item["generated_rrt_config_url"] = ProfilingRun._to_database_url(local_path)
        run_item["generated_rrt_config_size_bytes"] = len(yaml_text.encode("utf-8"))
        updated = True
        written_count += 1

    if updated:
        generated_run.rrt_config_execution_snapshot = snapshot
        generated_run.save(update_fields=["rrt_config_execution_snapshot", "updated_at"])
    return written_count


def _build_generated_run_rrt_snapshot(
    *,
    user_id: int,
    run_spec: dict[str, Any],
    runtime_by_name: dict[str, RuntimeEnvironment],
) -> dict[str, Any]:
    run_id = str(run_spec.get("run_id") or "").strip()
    runtime_env_name = str(run_spec.get("runtime_env_name") or "").strip()
    variant_values = run_spec.get("variant_values") if isinstance(run_spec.get("variant_values"), dict) else {}
    auto_overrides = _variant_values_to_rrt_overrides(variant_values)
    run_snapshot: dict[str, Any] = {
        "run_id": run_id,
        "runtime_env_name": runtime_env_name,
        "variant_values": variant_values,
        "auto_rrt_overrides": auto_overrides,
        "runtime_rrt_config_path": "",
        "runtime_has_rrt_config": False,
        "generated_rrt_config_yaml": "",
        "generated_rrt_config_json": {},
        "actions": [],
    }
    runtime_env = runtime_by_name.get(runtime_env_name)
    if runtime_env is None:
        run_snapshot["generation_error"] = f"Runtime environment '{runtime_env_name}' not found."
        return run_snapshot

    raw_rrt = str(runtime_env.serializer_rrt_config_raw_yaml or "").strip()
    run_snapshot["runtime_rrt_config_path"] = str(runtime_env.serializer_rrt_config_path or "")
    run_snapshot["runtime_has_rrt_config"] = bool(raw_rrt)
    if not raw_rrt and not auto_overrides:
        return run_snapshot

    base_payload: dict[str, Any] = {}
    if raw_rrt:
        try:
            loaded = yaml.safe_load(raw_rrt) or {}
        except yaml.YAMLError as exc:
            run_snapshot["generation_error"] = f"Invalid runtime serializer_rrt_config YAML: {exc}"
            return run_snapshot
        if not isinstance(loaded, dict):
            run_snapshot["generation_error"] = "Runtime serializer_rrt_config root must be an object."
            return run_snapshot
        base_payload = loaded

    merged = _dict_deep_merge(base_payload, auto_overrides) if auto_overrides else base_payload
    run_snapshot["generated_rrt_config_json"] = merged if isinstance(merged, dict) else {}
    run_snapshot["generated_rrt_config_yaml"] = yaml.safe_dump(merged, sort_keys=False)
    return run_snapshot


def _run_check_one_action(
    *,
    inventory_by_node: dict[str, dict[str, Any]],
    runtime_env_payload: dict[str, Any],
    scenario_snapshot: dict[str, Any],
    experiment_snapshot: dict[str, Any],
    phase: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    action_type = str(action.get("type") or "")
    target = str(action.get("target") or "")
    ctx = ExecutionContext(
        inventory_by_node=inventory_by_node,
        runtime_env=runtime_env_payload,
        scenario=scenario_snapshot,
        experiment=experiment_snapshot,
        phase={"name": str(phase.get("name") or "")},
        action=deepcopy(action),
    )
    executor = build_executor(ctx)
    executor.validate_input()

    if action_type == "run_runtime_preset":
        resolve_preset = getattr(executor, "_resolve_runtime_preset", None)
        if callable(resolve_preset):
            preset = resolve_preset()
            command = str(preset.get("command") or "")
            if not command.strip():
                raise ActionExecutionError(f"Runtime preset '{preset.get('name')}' has an empty command.")
            render_command = getattr(executor, "_render_command", None)
            if callable(render_command) and not bool(preset.get("skip_template_render")):
                render_command(command)

    return {
        "type": action_type,
        "target": target,
        "status": "READY",
        "ok": True,
        "message": "Action validation passed.",
    }


def _run_check_generated_payload(*, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    source_snapshots = payload.get("source_snapshots") if isinstance(payload.get("source_snapshots"), dict) else {}
    scenario_snapshot = source_snapshots.get("scenario") if isinstance(source_snapshots.get("scenario"), dict) else {}
    experiment_snapshot = source_snapshots.get("experiment") if isinstance(source_snapshots.get("experiment"), dict) else {}

    inventory_by_node, inventory_errors = _build_inventory_by_node(user_id, scenario_snapshot)
    check_results: list[dict[str, Any]] = []
    failed_runs = 0
    failed_actions = 0
    global_errors: list[str] = list(inventory_errors)

    for run_spec in runs:
        if not isinstance(run_spec, dict):
            failed_runs += 1
            global_errors.append("Generated run item must be an object.")
            continue

        run_id = str(run_spec.get("run_id") or "")
        runtime_env_name = str(run_spec.get("runtime_env_name") or "").strip()
        variant_values = run_spec.get("variant_values") if isinstance(run_spec.get("variant_values"), dict) else {}
        phases = run_spec.get("phases") if isinstance(run_spec.get("phases"), list) else []

        run_errors: list[str] = []
        runtime_env_payload, runtime_error = _resolve_runtime_env_payload(user_id, runtime_env_name)
        if runtime_error:
            run_errors.append(runtime_error)

        if inventory_errors:
            run_errors.extend(inventory_errors)

        phase_results: list[dict[str, Any]] = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            phase_name = str(phase.get("name") or "")
            actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
            action_results: list[dict[str, Any]] = []
            phase_ok = True

            for action in actions:
                if not isinstance(action, dict):
                    continue
                if run_errors:
                    phase_ok = False
                    failed_actions += 1
                    action_results.append(
                        {
                            "type": str(action.get("type") or ""),
                            "target": str(action.get("target") or ""),
                            "status": "FAILED",
                            "ok": False,
                            "message": "; ".join(run_errors),
                        }
                    )
                    continue
                try:
                    action_result = _run_check_one_action(
                        inventory_by_node=inventory_by_node,
                        runtime_env_payload=runtime_env_payload,
                        scenario_snapshot=scenario_snapshot,
                        experiment_snapshot=experiment_snapshot,
                        phase=phase,
                        action=action,
                    )
                except ActionExecutionError as exc:
                    phase_ok = False
                    failed_actions += 1
                    action_result = {
                        "type": str(action.get("type") or ""),
                        "target": str(action.get("target") or ""),
                        "status": "FAILED",
                        "ok": False,
                        "message": str(exc),
                    }
                except Exception as exc:
                    phase_ok = False
                    failed_actions += 1
                    action_result = {
                        "type": str(action.get("type") or ""),
                        "target": str(action.get("target") or ""),
                        "status": "FAILED",
                        "ok": False,
                        "message": f"Unexpected check error: {exc}",
                    }
                action_results.append(action_result)
            phase_results.append(
                {
                    "name": phase_name,
                    "status": "SUCCEEDED" if phase_ok else "FAILED",
                    "actions": action_results,
                }
            )

        run_ok = not run_errors and all(phase.get("status") == "SUCCEEDED" for phase in phase_results)
        if not run_ok:
            failed_runs += 1
            for message in run_errors:
                global_errors.append(f"{run_id or 'run-unknown'}: {message}")
            for phase in phase_results:
                if not isinstance(phase, dict):
                    continue
                for action in phase.get("actions") if isinstance(phase.get("actions"), list) else []:
                    if isinstance(action, dict) and not action.get("ok"):
                        global_errors.append(
                            f"{run_id or 'run-unknown'}:{phase.get('name') or 'phase'}:"
                            f"{action.get('type') or 'action'} -> {action.get('message') or 'validation failed'}"
                        )

        check_results.append(
            {
                "run_id": run_id,
                "runtime_env_name": runtime_env_name,
                "variant_values": variant_values,
                "status": "SUCCEEDED" if run_ok else "FAILED",
                "errors": run_errors,
                "phases": phase_results,
            }
        )

    return {
        "ok": failed_runs == 0,
        "total_runs": len(runs),
        "failed_runs": failed_runs,
        "failed_actions": failed_actions,
        "errors": global_errors,
        "runs": check_results,
        "run_summaries": _phase_summaries(payload),
    }


class ProfilingExperimentListCreateAPIView(APIView):
    """List experiments or upload one experiment plan YAML file."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = ProfilingExperiment.objects.filter(created_by=request.user).order_by("name")
        return Response(ProfilingExperimentSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = ProfilingExperimentUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        uploaded_file = serializer.validated_data["file"]

        raw_bytes = uploaded_file.read()
        raw_yaml = raw_bytes.decode("utf-8")

        try:
            payload = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError as exc:
            raise ValidationError({"file": "Invalid YAML content."}) from exc

        name, kind, description, scenario_name, execution, selections, phases = _extract_experiment_payload(payload)

        if not Scenario.objects.filter(created_by=user, name=scenario_name).exists():
            raise ValidationError({"scenarioRef": f"Scenario {scenario_name} was not found."})

        new_values = {
            "kind": kind,
            "description": description,
            "scenario_name": scenario_name,
            "execution": execution,
            "selections": selections,
            "phases": phases,
            "raw_payload": payload,
            "raw_yaml": raw_yaml,
            "generated_payload": {},
            "generated_yaml": "",
            "generated_source_refs": {},
            "generated_source_hashes": {},
            "total_generated_runs": 0,
            "generated_at": None,
            "run_check_status": ProfilingExperiment.RunCheckStatus.NOT_RUN,
            "run_check_report": {},
            "run_checked_at": None,
            "status": ProfilingExperiment.ExperimentStatus.NEW,
            "modified_by": user,
        }

        existing_exp = ProfilingExperiment.objects.filter(created_by=user, name=name).first()
        if existing_exp:
            invalidated_run_count = ProfilingRun.objects.filter(experiment=existing_exp).count()
            ProfilingRun.objects.filter(experiment=existing_exp).delete()
            changed_fields = _collect_field_updates(existing_exp, new_values)
            for field_name, value in new_values.items():
                setattr(existing_exp, field_name, value)
            existing_exp.save()
            created_items = []
            updated_items = [{"name": name, "updated_fields": changed_fields}] if changed_fields else []
        else:
            invalidated_run_count = 0
            ProfilingExperiment.objects.create(created_by=user, name=name, **new_values)
            created_items = [{"name": name, "scenario_name": scenario_name}]
            updated_items = []

        return Response(
            {
                "message": "Experiment plan uploaded successfully.",
                "created": {"experiments": created_items},
                "updated": {"experiments": updated_items},
                "invalidated": {"runs": invalidated_run_count},
                "total": {"experiments": ProfilingExperiment.objects.filter(created_by=user).count()},
            },
            status=status.HTTP_200_OK,
        )


class ProfilingExperimentDetailAPIView(APIView):
    """Get or delete one experiment definition by name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_exp(self, request, exp_name: str) -> ProfilingExperiment:
        return get_object_or_404(ProfilingExperiment, created_by=request.user, name=exp_name)

    def get(self, request, exp_name: str, *args, **kwargs):
        exp = self.get_exp(request, exp_name)
        body = ProfilingExperimentSerializer(exp).data
        body["topology_overview"] = exp.get_topology_overview(user=request.user)
        return Response(body, status=status.HTTP_200_OK)

    def delete(self, request, exp_name: str, *args, **kwargs):
        exp = self.get_exp(request, exp_name)
        exp.delete()
        return Response({"message": f"Deleted experiment {exp_name}."}, status=status.HTTP_200_OK)


class ProfilingExperimentGenerateAPIView(APIView):
    """Compile one experiment into generated payload and create pending run."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _parse_replace_run_id(request) -> int | None:
        payload = request.data if isinstance(request.data, Mapping) else {}
        raw_value = payload.get("replace_run_id")
        if raw_value in (None, ""):
            return None
        try:
            replace_run_id = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"replace_run_id": "replace_run_id must be an integer."}) from exc
        if replace_run_id <= 0:
            raise ValidationError({"replace_run_id": "replace_run_id must be >= 1."})
        return replace_run_id

    @staticmethod
    def _parse_replace_gen_version(request) -> int | None:
        payload = request.data if isinstance(request.data, Mapping) else {}
        raw_value = payload.get("replace_gen_version")
        if raw_value in (None, ""):
            return None
        try:
            replace_gen_version = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"replace_gen_version": "replace_gen_version must be an integer."}) from exc
        if replace_gen_version <= 0:
            raise ValidationError({"replace_gen_version": "replace_gen_version must be >= 1."})
        return replace_gen_version

    def post(self, request, exp_name: str, *args, **kwargs):
        exp = get_object_or_404(ProfilingExperiment, created_by=request.user, name=exp_name)
        scenario = get_object_or_404(Scenario, created_by=request.user, name=exp.scenario_name)
        replace_run_id = self._parse_replace_run_id(request)
        replace_gen_version = self._parse_replace_gen_version(request)
        if replace_run_id is not None and replace_gen_version is not None:
            raise ValidationError(
                {"replace": "Specify only one of replace_run_id or replace_gen_version."}
            )
        replace_target_run: ProfilingRun | None = None
        replace_generation_runs: list[ProfilingRun] = []
        if replace_gen_version is not None:
            replace_generation_runs = list(
                ProfilingRun.objects.filter(
                    experiment=exp,
                    requested_by=request.user,
                    gen_version=replace_gen_version,
                ).only("id", "status", "plan_run_id", "gen_version", "collected_metrics_path", "results")
            )
            if not replace_generation_runs:
                raise ValidationError(
                    {
                        "replace_gen_version": (
                            f"Generation v{replace_gen_version} was not found for experiment {exp_name}."
                        )
                    }
                )
            has_running = any(run.status == ProfilingRun.RunStatus.RUNNING for run in replace_generation_runs)
            if has_running:
                return Response(
                    {
                        "message": (
                            f"Generation v{replace_gen_version} has RUNNING runs and cannot be replaced yet. "
                            "Wait for completion and retry."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        if replace_run_id is not None:
            replace_target_run = (
                ProfilingRun.objects.filter(
                    experiment=exp,
                    requested_by=request.user,
                    id=replace_run_id,
                )
                .only("id", "status", "plan_run_id", "gen_version")
                .first()
            )
            if replace_target_run is None:
                raise ValidationError(
                    {"replace_run_id": f"Run {replace_run_id} was not found for experiment {exp_name}."}
                )
            if replace_target_run.status == ProfilingRun.RunStatus.RUNNING:
                return Response(
                    {
                        "message": (
                            f"Run {replace_run_id} is RUNNING and cannot be replaced yet. "
                            "Wait for completion and retry."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if replace_target_run.status != ProfilingRun.RunStatus.PENDING:
                raise ValidationError(
                    {
                        "replace_run_id": (
                            f"Run {replace_run_id} is {replace_target_run.status}. "
                            "Only pre-generated PENDING runs can be replaced."
                        )
                    }
                )

        experiment_payload = exp.raw_payload if isinstance(exp.raw_payload, dict) else {}
        scenario_payload = scenario.raw_payload if isinstance(scenario.raw_payload, dict) else {}
        if not experiment_payload:
            raise ValidationError({"experiment": "Stored experiment payload is empty."})
        if not scenario_payload:
            raise ValidationError({"scenario": "Stored scenario payload is empty."})

        try:
            compiled_payload = compile_experiment_plan(
                generated_name=exp.name,
                experiment_payload=experiment_payload,
                scenario_payload=scenario_payload,
            )
        except ExecPipelineCompileError as exc:
            raise ValidationError({"compile": str(exc)}) from exc

        compiled_runs_all = compiled_payload.get("runs") if isinstance(compiled_payload.get("runs"), list) else []
        materialized_payload = compiled_payload
        replaced_plan_run_id = ""
        replaced_run_id_value = replace_target_run.id if replace_target_run is not None else None
        replaced_gen_version_value = replace_gen_version
        if replace_gen_version is not None:
            materialized_payload = compiled_payload
        elif replace_target_run is not None:
            replaced_plan_run_id = str(replace_target_run.plan_run_id or "").strip()
            if not replaced_plan_run_id:
                raise ValidationError(
                    {"replace_run_id": f"Run {replace_target_run.id} has no plan_run_id and cannot be replaced."}
                )
            matched_run_spec = None
            for run_index, run_item in enumerate(compiled_runs_all, start=1):
                if not isinstance(run_item, dict):
                    continue
                plan_run_id = str(run_item.get("run_id") or f"run-{run_index:03d}")
                if plan_run_id == replaced_plan_run_id:
                    matched_run_spec = run_item
                    break
            if matched_run_spec is None:
                raise ValidationError(
                    {
                        "replace_run_id": (
                            f"Run {replace_target_run.id} (plan_run_id={replaced_plan_run_id}) "
                            "is not present in the latest compiled plan."
                        )
                    }
                )
            materialized_payload = _single_run_payload_snapshot(
                compiled_payload=compiled_payload,
                run_spec=matched_run_spec,
            )

        rrt_variable_checks = _collect_rrt_variable_checks(
            user_id=request.user.id,
            compiled_payload=materialized_payload,
        )
        if not rrt_variable_checks.get("ok"):
            raise ValidationError(
                {
                    "rrt_variables": (
                        "Variant variables do not match serializer_rrt_config.yaml keys for one or more runs."
                    ),
                    "failed_runs": rrt_variable_checks.get("failed_runs") or [],
                    "checks": rrt_variable_checks.get("runs") or [],
                }
            )

        compiled_yaml = render_generated_plan_yaml(materialized_payload)
        new_gen_version = (
            int(replace_gen_version)
            if replace_gen_version is not None
            else int(exp.current_gen_version or 0) + 1
        )
        exp.generated_payload = materialized_payload
        exp.generated_yaml = compiled_yaml
        exp.generated_source_refs = materialized_payload.get("source_refs") or {}
        exp.generated_source_hashes = {
            "experiment_sha256": _stable_sha256(experiment_payload),
            "scenario_sha256": _stable_sha256(scenario_payload),
        }
        exp.total_generated_runs = int(materialized_payload.get("total_generated_runs", 0) or 0)
        exp.current_gen_version = new_gen_version
        exp.generated_at = timezone.now()
        exp.run_check_status = ProfilingExperiment.RunCheckStatus.NOT_RUN
        exp.run_check_report = {}
        exp.run_checked_at = None
        exp.modified_by = request.user
        exp.save(
            update_fields=[
                "generated_payload",
                "generated_yaml",
                "generated_source_refs",
                "generated_source_hashes",
                "total_generated_runs",
                "current_gen_version",
                "generated_at",
                "run_check_status",
                "run_check_report",
                "run_checked_at",
                "modified_by",
                "updated_at",
            ]
        )

        purged_pending_runs = 0
        replaced_deleted_artifact_dirs: list[str] = []
        replaced_missing_artifact_dirs: list[str] = []
        replacement_artifact_errors: list[str] = []
        if replace_gen_version is not None:
            for old_run in replace_generation_runs:
                deleted_dirs, missing_dirs, errors = delete_run_artifacts(old_run)
                replaced_deleted_artifact_dirs.extend(deleted_dirs)
                replaced_missing_artifact_dirs.extend(missing_dirs)
                replacement_artifact_errors.extend(errors)
            purged_pending_runs = len(replace_generation_runs)
            ProfilingRun.objects.filter(
                experiment=exp,
                requested_by=request.user,
                gen_version=replace_gen_version,
            ).delete()
        elif replace_target_run is not None:
            (
                replaced_deleted_artifact_dirs,
                replaced_missing_artifact_dirs,
                replacement_artifact_errors,
            ) = delete_run_artifacts(replace_target_run)
            replace_target_run.delete()
            purged_pending_runs = 1
        compiled_runs = materialized_payload.get("runs") if isinstance(materialized_payload.get("runs"), list) else []
        stop_on_failure = bool((materialized_payload.get("execution_policy") or {}).get("stop_on_failure", True))
        runtime_names = {
            str(run.get("runtime_env_name") or "").strip()
            for run in compiled_runs
            if isinstance(run, dict) and str(run.get("runtime_env_name") or "").strip()
        }
        runtime_by_name = {
            env.name: env
            for env in RuntimeEnvironment.objects.filter(created_by_id=request.user.id, name__in=sorted(runtime_names))
        }
        pending_runs: list[ProfilingRun] = []
        generated_rrt_preview_files = 0
        for run_index, run_item in enumerate(compiled_runs, start=1):
            if not isinstance(run_item, dict):
                continue
            plan_run_id = str(run_item.get("run_id") or f"run-{run_index:03d}")
            run_snapshot = _single_run_payload_snapshot(
                compiled_payload=materialized_payload,
                run_spec=run_item,
            )
            run_metadata = _build_run_metadata(run_spec=run_item, plan_run_id=plan_run_id)
            run_rrt_snapshot = _build_generated_run_rrt_snapshot(
                user_id=request.user.id,
                run_spec=run_item,
                runtime_by_name=runtime_by_name,
            )
            run_characterization = ProfilingRun.build_characterization_parameters(
                plan_run_id=plan_run_id,
                runtime_env_name=str(run_item.get("runtime_env_name") or "").strip(),
                variant_values=run_item.get("variant_values") if isinstance(run_item.get("variant_values"), dict) else {},
                edge=run_item.get("edge") if isinstance(run_item.get("edge"), dict) else {},
                rrt_config_payload=(
                    run_rrt_snapshot.get("generated_rrt_config_json")
                    if isinstance(run_rrt_snapshot.get("generated_rrt_config_json"), dict)
                    else {}
                ),
            )
            pending_run = _create_pending_run(
                experiment=exp,
                requested_by_id=request.user.id,
                gen_version=new_gen_version,
                plan_run_id=plan_run_id,
                compiled_payload_snapshot=run_snapshot,
                run_metadata=run_metadata,
                characterization_parameters=run_characterization,
                total_runs=1,
                stop_on_failure=stop_on_failure,
                rrt_config_execution_snapshot={
                    "runs": {
                        plan_run_id: run_rrt_snapshot
                    }
                },
            )
            pending_runs.append(pending_run)
            try:
                generated_rrt_preview_files += _write_generated_rrt_config_preview_files(
                    generated_run=pending_run
                )
            except (OSError, ValueError) as exc:
                pending_run.delete()
                raise ValidationError(
                    {
                        "rrt_config_preview": (
                            f"Failed to write generated RRT preview file for run '{plan_run_id}': {exc}"
                        )
                    }
                ) from exc

        exp.sync_status_from_runs(save=True, modified_by=request.user)
        response_message = (
            f"Replaced generation v{replace_gen_version} for experiment {exp_name}."
            if replace_gen_version is not None
            else (
                f"Generated replacement run for experiment {exp_name}."
                if replace_target_run is not None
                else f"Generated concrete execution plan for experiment {exp_name}."
            )
        )

        return Response(
            {
                "message": response_message,
                "experiment": ProfilingExperimentSerializer(exp).data,
                "pending_runs": ProfilingRunSerializer(pending_runs, many=True).data,
                "created_pending_runs": len(pending_runs),
                "purged_pending_runs": purged_pending_runs,
                "replace_mode": (replace_target_run is not None) or (replace_gen_version is not None),
                "replaced_run_id": replaced_run_id_value,
                "replaced_gen_version": replaced_gen_version_value,
                "replaced_plan_run_id": replaced_plan_run_id,
                "replaced_deleted_artifact_dirs": replaced_deleted_artifact_dirs,
                "replaced_missing_artifact_dirs": replaced_missing_artifact_dirs,
                "replacement_artifact_errors": replacement_artifact_errors,
                "gen_version": new_gen_version,
                "compiled_yaml": compiled_yaml,
                "total_generated_runs": materialized_payload.get("total_generated_runs", 0),
                "generated_rrt_preview_files": generated_rrt_preview_files,
                "run_summaries": _phase_summaries(materialized_payload),
                "rrt_variable_checks": rrt_variable_checks,
            },
            status=status.HTTP_200_OK,
        )


class ProfilingRunListAPIView(APIView):
    """List profiling execution runs."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = (
            ProfilingRun.objects.select_related("experiment")
            .filter(requested_by=request.user)
            .order_by("-created_at")
        )
        exp_name = str(request.query_params.get("exp_name") or request.query_params.get("experiment_name") or "").strip()
        if exp_name:
            queryset = queryset.filter(experiment__name=exp_name)
        raw_gen_version = str(request.query_params.get("gen_version") or "").strip()
        if raw_gen_version:
            try:
                gen_version = int(raw_gen_version)
            except ValueError as exc:
                raise ValidationError({"gen_version": "gen_version must be an integer."}) from exc
            if gen_version <= 0:
                raise ValidationError({"gen_version": "gen_version must be >= 1."})
            queryset = queryset.filter(gen_version=gen_version)
        return Response(ProfilingRunSerializer(queryset, many=True).data, status=status.HTTP_200_OK)


class ProfilingComparisionListCreateAPIView(APIView):
    """List and create saved run comparisons."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = (
            ProfilingComparision.objects.filter(created_by=request.user)
            .prefetch_related("runs", "runs__run", "runs__run__experiment")
            .order_by("-created_at", "-id")
        )
        return Response(ProfilingComparisionSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        input_serializer = ProfilingComparisionCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        payload = input_serializer.validated_data

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "name is required."})
        description = str(payload.get("description") or "").strip()

        raw_run_ids = payload.get("run_ids") if isinstance(payload.get("run_ids"), list) else []
        run_ids: list[int] = []
        for run_id in raw_run_ids:
            run_id_int = int(run_id)
            if run_id_int not in run_ids:
                run_ids.append(run_id_int)
        if not run_ids:
            raise ValidationError({"run_ids": "At least one run id is required."})

        runs = list(
            ProfilingRun.objects.select_related("experiment")
            .filter(requested_by=request.user, id__in=run_ids)
            .order_by("id")
        )
        runs_by_id = {int(run.id): run for run in runs}
        missing = [run_id for run_id in run_ids if run_id not in runs_by_id]
        if missing:
            raise ValidationError({"run_ids": f"Run(s) not found or unauthorized: {missing}"})

        try:
            comparision = ProfilingComparision.objects.create(
                name=name,
                description=description,
                created_by=request.user,
                modified_by=request.user,
            )
        except IntegrityError as exc:
            raise ValidationError({"name": f"Comparision '{name}' already exists."}) from exc

        rows: list[ProfilingComparisionRun] = []
        for run_id in run_ids:
            run_obj = runs_by_id[run_id]
            rows.append(
                ProfilingComparisionRun(
                    comparision=comparision,
                    run=run_obj,
                    run_id_snapshot=run_obj.id,
                    experiment_name_snapshot=str(run_obj.experiment.name or ""),
                    plan_run_id_snapshot=str(run_obj.plan_run_id or ""),
                    status_snapshot=str(run_obj.status or ""),
                )
            )
        if rows:
            ProfilingComparisionRun.objects.bulk_create(rows)

        comparision.refresh_from_db()
        return Response(ProfilingComparisionSerializer(comparision).data, status=status.HTTP_201_CREATED)


class ProfilingRunDetailAPIView(APIView):
    """Get one profiling run by id."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, run_id: int, *args, **kwargs):
        run_obj = get_object_or_404(
            ProfilingRun.objects.select_related("experiment"),
            id=run_id,
            requested_by=request.user,
        )
        try:
            _write_generated_rrt_config_preview_files(generated_run=run_obj)
        except (OSError, ValueError):
            pass
        return Response(
            ProfilingRunSerializer(run_obj, context={"include_run_metric_files": True}).data,
            status=status.HTTP_200_OK,
        )

    def patch(self, request, run_id: int, *args, **kwargs):
        run_obj = get_object_or_404(
            ProfilingRun.objects.select_related("experiment"),
            id=run_id,
            requested_by=request.user,
        )
        payload = request.data if isinstance(request.data, Mapping) else {}
        raw_note = payload.get("note", "")
        if raw_note is None:
            note = ""
        elif isinstance(raw_note, str):
            note = raw_note
        else:
            raise ValidationError({"note": "note must be a string."})
        if len(note) > 4000:
            raise ValidationError({"note": "note must be <= 4000 characters."})

        run_obj.note = note
        run_obj.save(update_fields=["note", "updated_at"])
        return Response(
            {
                "message": f"Updated run {run_id} note.",
                "run": ProfilingRunSerializer(run_obj).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, run_id: int, *args, **kwargs):
        run_obj = get_object_or_404(
            ProfilingRun.objects.select_related("experiment"),
            id=run_id,
            requested_by=request.user,
        )
        if run_obj.status in {ProfilingRun.RunStatus.PENDING, ProfilingRun.RunStatus.RUNNING}:
            return Response(
                {
                    "message": (
                        f"Run {run_id} is {run_obj.status} and cannot be deleted yet. "
                        "Wait for completion and retry."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        deleted_dirs, missing_dirs, errors = delete_run_artifacts(run_obj)
        if errors:
            return Response(
                {
                    "message": f"Failed to delete all artifacts for run {run_id}.",
                    "deleted_artifact_dirs": deleted_dirs,
                    "missing_artifact_dirs": missing_dirs,
                    "errors": errors,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        experiment = run_obj.experiment
        run_obj.delete()
        experiment.sync_status_from_runs(save=True, modified_by=request.user)
        return Response(
            {
                "message": f"Deleted run {run_id}.",
                "deleted_run_id": run_id,
                "deleted_artifact_dirs": deleted_dirs,
                "missing_artifact_dirs": missing_dirs,
            },
            status=status.HTTP_200_OK,
        )


class ProfilingExperimentRunListDeleteAPIView(APIView):
    """List/delete profiling runs for one experiment."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _parse_optional_gen_version(request) -> int | None:
        raw_value = str(request.query_params.get("gen_version") or "").strip()
        if not raw_value:
            return None
        try:
            gen_version = int(raw_value)
        except ValueError as exc:
            raise ValidationError({"gen_version": "gen_version must be an integer."}) from exc
        if gen_version <= 0:
            raise ValidationError({"gen_version": "gen_version must be >= 1."})
        return gen_version

    def delete(self, request, exp_name: str, *args, **kwargs):
        exp = get_object_or_404(ProfilingExperiment, created_by=request.user, name=exp_name)
        target_gen_version = self._parse_optional_gen_version(request)
        queryset = ProfilingRun.objects.select_related("experiment").filter(
            requested_by=request.user,
            experiment=exp,
        )
        if target_gen_version is not None:
            queryset = queryset.filter(gen_version=target_gen_version)
        runs = list(
            queryset.order_by("id")
        )
        if not runs:
            message = (
                f"No runs found for experiment {exp_name} generation v{target_gen_version}."
                if target_gen_version is not None
                else f"No runs found for experiment {exp_name}."
            )
            return Response(
                {
                    "message": message,
                    "experiment_name": exp_name,
                    "gen_version": target_gen_version,
                    "total_found": 0,
                    "deleted_run_ids": [],
                    "skipped_active_run_ids": [],
                    "deleted_artifact_dirs": [],
                    "missing_artifact_dirs": [],
                    "errors": [],
                },
                status=status.HTTP_200_OK,
            )

        deleted_run_ids: list[int] = []
        skipped_active_run_ids: list[int] = []
        deleted_artifact_dirs: list[str] = []
        missing_artifact_dirs: list[str] = []
        errors: list[str] = []

        for run_obj in runs:
            should_skip_active = (
                run_obj.status in {ProfilingRun.RunStatus.PENDING, ProfilingRun.RunStatus.RUNNING}
                if target_gen_version is None
                else run_obj.status == ProfilingRun.RunStatus.RUNNING
            )
            if should_skip_active:
                skipped_active_run_ids.append(run_obj.id)
                continue

            run_deleted_dirs, run_missing_dirs, run_errors = delete_run_artifacts(run_obj)
            deleted_artifact_dirs.extend(run_deleted_dirs)
            missing_artifact_dirs.extend(run_missing_dirs)
            if run_errors:
                errors.extend([f"run {run_obj.id}: {item}" for item in run_errors])
                continue

            deleted_run_ids.append(run_obj.id)
            run_obj.delete()

        response_status = status.HTTP_200_OK if not errors else status.HTTP_500_INTERNAL_SERVER_ERROR
        exp.sync_status_from_runs(save=True, modified_by=request.user)
        success_message = (
            f"Deleted {len(deleted_run_ids)} run(s) for experiment {exp_name} generation v{target_gen_version}."
            if target_gen_version is not None
            else f"Deleted {len(deleted_run_ids)} run(s) for experiment {exp_name}."
        )
        partial_message = (
            f"Partially deleted runs for experiment {exp_name} generation v{target_gen_version}."
            if target_gen_version is not None
            else f"Partially deleted runs for experiment {exp_name}."
        )
        return Response(
            {
                "message": (
                    success_message
                    if not errors
                    else partial_message
                ),
                "experiment_name": exp_name,
                "gen_version": target_gen_version,
                "total_found": len(runs),
                "deleted_run_ids": deleted_run_ids,
                "skipped_active_run_ids": skipped_active_run_ids,
                "deleted_artifact_dirs": sorted(set(deleted_artifact_dirs)),
                "missing_artifact_dirs": sorted(set(missing_artifact_dirs)),
                "errors": errors,
            },
            status=response_status,
        )


class ProfilingRunRerunAPIView(APIView):
    """Create and start a rerun for one historical run."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, run_id: int, *args, **kwargs):
        payload = request.data if isinstance(request.data, Mapping) else {}
        raw_replace = payload.get("replace_current_run", False)
        if isinstance(raw_replace, bool):
            replace_current_run = raw_replace
        elif isinstance(raw_replace, str):
            normalized = raw_replace.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                replace_current_run = True
            elif normalized in {"false", "0", "no", "n", ""}:
                replace_current_run = False
            else:
                raise ValidationError({"replace_current_run": "replace_current_run must be a boolean."})
        else:
            raise ValidationError({"replace_current_run": "replace_current_run must be a boolean."})

        source_run = get_object_or_404(
            ProfilingRun.objects.select_related("experiment"),
            id=run_id,
            requested_by=request.user,
        )
        if source_run.status == ProfilingRun.RunStatus.RUNNING:
            return Response(
                {"message": f"Run {run_id} is RUNNING and cannot be rerun yet."},
                status=status.HTTP_409_CONFLICT,
            )

        exp = source_run.experiment
        running_exists = ProfilingRun.objects.filter(
            experiment=exp,
            requested_by=request.user,
            status=ProfilingRun.RunStatus.RUNNING,
        ).exists()
        if running_exists:
            return Response(
                {
                    "message": (
                        f"Experiment {exp.name} already has a RUNNING run. "
                        "Wait for completion before rerunning another run."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        cloned_payload = (
            deepcopy(source_run.compiled_payload_snapshot)
            if isinstance(source_run.compiled_payload_snapshot, dict)
            else {}
        )
        run_metadata = deepcopy(source_run.run_metadata) if isinstance(source_run.run_metadata, dict) else {}
        characterization_parameters = (
            deepcopy(source_run.characterization_parameters)
            if isinstance(source_run.characterization_parameters, dict)
            else {}
        )
        rrt_snapshot = (
            deepcopy(source_run.rrt_config_execution_snapshot)
            if isinstance(source_run.rrt_config_execution_snapshot, dict)
            else {}
        )
        deleted_dirs: list[str] = []
        missing_dirs: list[str] = []
        rerun: ProfilingRun
        if replace_current_run:
            deleted_dirs, missing_dirs, errors = delete_run_artifacts(source_run)
            if errors:
                return Response(
                    {
                        "message": f"Failed to reset artifacts for run {run_id}.",
                        "deleted_artifact_dirs": deleted_dirs,
                        "missing_artifact_dirs": missing_dirs,
                        "errors": errors,
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            rerun = source_run
            rerun.status = ProfilingRun.RunStatus.PENDING
            rerun.started_at = None
            rerun.finished_at = None
            rerun.error_message = ""
            rerun.task_id = ""
            rerun.metrics_collected = False
            rerun.results = []
            rerun.phase_action_execution_status = {}
            rerun.compiled_payload_snapshot = cloned_payload
            rerun.run_metadata = run_metadata
            rerun.characterization_parameters = characterization_parameters
            rerun.rrt_config_execution_snapshot = rrt_snapshot
            rerun.total_runs = max(1, int(source_run.total_runs or 1))
            rerun.completed_runs = 0
            rerun.failed_runs = 0
            rerun.stop_on_failure = bool(source_run.stop_on_failure)
            rerun.collected_metrics_path = ""
            rerun.save(
                update_fields=[
                    "status",
                    "started_at",
                    "finished_at",
                    "error_message",
                    "task_id",
                    "metrics_collected",
                    "results",
                    "phase_action_execution_status",
                    "compiled_payload_snapshot",
                    "run_metadata",
                    "characterization_parameters",
                    "rrt_config_execution_snapshot",
                    "total_runs",
                    "completed_runs",
                    "failed_runs",
                    "stop_on_failure",
                    "collected_metrics_path",
                    "updated_at",
                ]
            )
            try:
                _write_generated_rrt_config_preview_files(generated_run=rerun)
            except (OSError, ValueError) as exc:
                raise ValidationError({"rerun": f"Failed to prepare rerun artifacts: {exc}"}) from exc

            exp.start_count += 1
            exp.last_started_at = timezone.now()
            exp.modified_by = request.user
            exp.save(
                update_fields=[
                    "start_count",
                    "last_started_at",
                    "modified_by",
                    "updated_at",
                ]
            )
        else:
            next_gen_version = int(exp.current_gen_version or 0) + 1
            rerun = _create_pending_run(
                experiment=exp,
                requested_by_id=request.user.id,
                gen_version=next_gen_version,
                plan_run_id=source_run.plan_run_id,
                compiled_payload_snapshot=cloned_payload,
                run_metadata=run_metadata,
                characterization_parameters=characterization_parameters,
                total_runs=max(1, int(source_run.total_runs or 1)),
                stop_on_failure=bool(source_run.stop_on_failure),
                rrt_config_execution_snapshot=rrt_snapshot,
            )
            try:
                _write_generated_rrt_config_preview_files(generated_run=rerun)
            except (OSError, ValueError) as exc:
                rerun.delete()
                raise ValidationError({"rerun": f"Failed to prepare rerun artifacts: {exc}"}) from exc

            generated_yaml = ""
            if isinstance(cloned_payload, dict) and cloned_payload:
                try:
                    generated_yaml = render_generated_plan_yaml(cloned_payload)
                except Exception:
                    generated_yaml = ""
            exp.generated_payload = cloned_payload
            if generated_yaml:
                exp.generated_yaml = generated_yaml
            exp.total_generated_runs = max(1, int(cloned_payload.get("total_generated_runs", 1) or 1))
            exp.current_gen_version = next_gen_version
            exp.generated_at = timezone.now()
            exp.start_count += 1
            exp.last_started_at = timezone.now()
            exp.modified_by = request.user
            exp.save(
                update_fields=[
                    "generated_payload",
                    "generated_yaml",
                    "total_generated_runs",
                    "current_gen_version",
                    "generated_at",
                    "start_count",
                    "last_started_at",
                    "modified_by",
                    "updated_at",
                ]
            )

        rerun.status = ProfilingRun.RunStatus.RUNNING
        rerun.started_at = timezone.now()
        rerun.finished_at = None
        rerun.error_message = ""
        rerun.task_id = ""
        rerun.save(
            update_fields=["status", "started_at", "finished_at", "error_message", "task_id", "updated_at"]
        )
        exp.sync_status_from_runs(save=True, modified_by=request.user)

        queued = False
        sync_fallback = False
        queue_error = ""
        task_id = ""
        try:
            task = execute_generated_plan_run_task.apply_async(
                kwargs={"generated_run_id": rerun.id},
                retry=False,
            )
        except Exception as exc:
            queue_error = str(exc)
            sync_fallback = True
            execute_generated_plan_run_task.run(generated_run_id=rerun.id)
            rerun.refresh_from_db()
        else:
            task_id = task.id or ""
            rerun.task_id = task_id
            rerun.save(update_fields=["task_id", "updated_at"])
            queued = True

        return Response(
            {
                "message": (
                    f"Run {run_id} was reset and rerun started in-place."
                    if replace_current_run
                    else f"Rerun created from run {run_id}."
                ),
                "source_run_id": source_run.id,
                "rerun_run_id": rerun.id,
                "gen_version": rerun.gen_version,
                "replaced_current_run": replace_current_run,
                "queued": queued,
                "sync_fallback": sync_fallback,
                "queue_error": queue_error,
                "task_id": task_id,
                "deleted_artifact_dirs": deleted_dirs,
                "missing_artifact_dirs": missing_dirs,
                "run": ProfilingRunSerializer(rerun).data,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ProfilingRunMetricFileAPIView(APIView):
    """Download one run metric artifact file."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    _ALLOWED_FILES: dict[str, str] = {
        "rrt_summary.json": "application/json",
        "rrt_config.yaml": "application/x-yaml",
        "rrt_all.csv": "text/csv",
    }

    def get(self, request, run_id: int, run_label: str, file_name: str, *args, **kwargs):
        run_obj = get_object_or_404(
            ProfilingRun.objects.select_related("experiment"),
            id=run_id,
            requested_by=request.user,
        )
        file_key = str(file_name or "").strip()
        if file_key not in self._ALLOWED_FILES:
            return Response({"message": f"Unsupported metric file: {file_key}"}, status=status.HTTP_404_NOT_FOUND)

        finder = getattr(run_obj, "find_run_metric_file_path", None)
        metric_path = finder(run_label, file_key) if callable(finder) else None
        if not isinstance(metric_path, Path) or not metric_path.is_file():
            return Response(
                {"message": f"Metric file {file_key} not found for run {run_label}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        response = FileResponse(open(metric_path, "rb"), content_type=self._ALLOWED_FILES[file_key])
        response["Content-Disposition"] = f'inline; filename="{file_key}"'
        return response


class ProfilingExperimentStartAPIView(APIView):
    """Start execution for one experiment pending run."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _parse_required_gen_version(request) -> int:
        payload = request.data if isinstance(request.data, Mapping) else {}
        raw_value = payload.get("gen_version")
        if raw_value in (None, ""):
            raise ValidationError({"gen_version": "gen_version is required."})
        try:
            gen_version = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"gen_version": "gen_version must be an integer."}) from exc
        if gen_version <= 0:
            raise ValidationError({"gen_version": "gen_version must be >= 1."})
        return gen_version

    @staticmethod
    def _parse_requested_run_id(request) -> int | None:
        payload = request.data if isinstance(request.data, Mapping) else {}
        raw_value = payload.get("run_id")
        if raw_value in (None, ""):
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"run_id": "run_id must be an integer."}) from exc

    def post(self, request, exp_name: str, *args, **kwargs):
        exp = get_object_or_404(ProfilingExperiment, created_by=request.user, name=exp_name)
        payload = exp.generated_payload if isinstance(exp.generated_payload, dict) else {}
        runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
        if not runs:
            raise ValidationError({"experiment": "Experiment has no generated run payload. Call exp gen first."})
        requested_gen_version = self._parse_required_gen_version(request)
        requested_run_id = self._parse_requested_run_id(request)

        running_exists = ProfilingRun.objects.filter(
            experiment=exp,
            requested_by=request.user,
            status=ProfilingRun.RunStatus.RUNNING,
        ).exists()
        if running_exists:
            return Response(
                {
                    "message": (
                        f"Experiment {exp_name} already has a running run. "
                        "Wait for it to finish before starting again."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        pending_runs = list(
            ProfilingRun.objects.filter(
                experiment=exp,
                requested_by=request.user,
                status=ProfilingRun.RunStatus.PENDING,
                gen_version=requested_gen_version,
            )
            .order_by("created_at", "id")
        )
        if not pending_runs:
            raise ValidationError(
                {
                    "experiment": (
                        f"No pending run found for generation {requested_gen_version}. "
                        "Call exp gen first or choose an existing generation."
                    )
                }
            )

        run_obj = None
        if requested_run_id is not None:
            run_obj = next((item for item in pending_runs if item.id == requested_run_id), None)
            if run_obj is None:
                requested_run = (
                    ProfilingRun.objects.filter(
                        experiment=exp,
                        requested_by=request.user,
                        id=requested_run_id,
                    )
                    .only("id", "status", "gen_version")
                    .first()
                )
                if requested_run is None:
                    raise ValidationError({"run_id": f"Run {requested_run_id} was not found for experiment {exp_name}."})
                return Response(
                    {
                        "message": (
                            f"Run {requested_run_id} is gen_version={requested_run.gen_version}, status={requested_run.status} "
                            f"and cannot be started for gen_version={requested_gen_version}. "
                            "Only PENDING runs in the requested generation can be started."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        else:
            run_obj = pending_runs[0]

        exp.start_count += 1
        exp.last_started_at = timezone.now()
        exp.modified_by = request.user
        exp.save(update_fields=["start_count", "last_started_at", "modified_by", "updated_at"])

        run_obj.status = ProfilingRun.RunStatus.RUNNING
        run_obj.started_at = timezone.now()
        run_obj.finished_at = None
        run_obj.error_message = ""
        run_obj.task_id = ""
        run_obj.save(
            update_fields=["status", "started_at", "finished_at", "error_message", "task_id", "updated_at"]
        )
        exp.sync_status_from_runs(save=True, modified_by=request.user)

        queued_runs: list[dict[str, Any]] = []
        sync_fallback_runs: list[dict[str, Any]] = []
        queue_failed = None
        task = None
        try:
            task = execute_generated_plan_run_task.apply_async(
                kwargs={"generated_run_id": run_obj.id},
                retry=False,
            )
        except Exception as exc:  # fallback when broker is unavailable
            queue_failed = str(exc)

        if queue_failed is not None:
            result = execute_generated_plan_run_task.run(generated_run_id=run_obj.id)
            run_obj.refresh_from_db()
            sync_fallback_runs.append(
                {
                    "run": ProfilingRunSerializer(run_obj).data,
                    "queue_error": queue_failed,
                    "result": result,
                }
            )
        else:
            run_obj.task_id = task.id or ""
            run_obj.save(update_fields=["task_id", "updated_at"])
            queued_runs.append(ProfilingRunSerializer(run_obj).data)

        return Response(
            {
                "message": f"Experiment {exp_name} start accepted.",
                "execution_mode": "queued",
                "queued_runs": queued_runs,
                "sync_fallback_runs": sync_fallback_runs,
                "queued_count": len(queued_runs),
                "sync_fallback_count": len(sync_fallback_runs),
                "started_run_id": run_obj.id,
                "gen_version": run_obj.gen_version,
                "remaining_pending_runs": max(0, len(pending_runs) - 1),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ProfilingExperimentRunCheckAPIView(APIView):
    """Run dry validation checks for one experiment generated payload."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, exp_name: str, *args, **kwargs):
        exp = get_object_or_404(ProfilingExperiment, created_by=request.user, name=exp_name)
        payload = exp.generated_payload if isinstance(exp.generated_payload, dict) else {}
        runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
        if not runs:
            raise ValidationError({"experiment": "Experiment has no generated runs to validate. Call exp gen first."})

        check_report = _run_check_generated_payload(user_id=request.user.id, payload=payload)
        checked_at = timezone.now()
        exp.update_run_check(check_report, checked_at=checked_at)
        exp.modified_by = request.user
        exp.save(update_fields=["run_check_status", "run_check_report", "run_checked_at", "modified_by", "updated_at"])
        return Response(
            {
                "message": (
                    f"Experiment {exp_name} run check passed."
                    if check_report.get("ok")
                    else f"Experiment {exp_name} run check found issues."
                ),
                "experiment_name": exp_name,
                "run_check_status": exp.run_check_status,
                "run_checked_at": exp.run_checked_at,
                **check_report,
            },
            status=status.HTTP_200_OK,
        )

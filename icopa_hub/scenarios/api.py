"""APIs for scenario management."""

from __future__ import annotations

from datetime import timedelta
from django.core.files.base import ContentFile
import logging
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
import yaml

from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import (
    is_preset_containers_fallback_enabled,
    resolve_preset_container,
)
from inventory.models import VM
from icopa_core.connectors.vm_ssh_client import build_vm_ssh_client
from icopa_core.task_executor.action_center import (
    ActionCenterError,
    normalize_action_payload,
)
from icopa_core.task_executor.vm_runtime_actions.vm_zenoh_routing_setup import (
    builtin_zenoh_routing_preset_names,
)
from runtime_env.models import RuntimeEnvironment

from .celery_tasks import validate_scenario_task
from .models import Scenario, ScenarioValidationRun
from .serializers import ScenarioSerializer, ScenarioUploadSerializer

logger = logging.getLogger(__name__)
_COLLECT_PHASE_NAMES = {"collect_metrics"}


def _api_debug(event: str, **meta) -> None:
    line = f"[scenarios-api] {event} | {meta}"
    print(line, flush=True)
    logger.info(line)


def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _task_state(task_id: str) -> str:
    if not task_id:
        return "UNKNOWN"
    try:
        return str(validate_scenario_task.AsyncResult(task_id).state or "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def _next_check_status(current_status: str, requested: bool, in_progress_status: str) -> str:
    if requested:
        return in_progress_status
    return current_status or Scenario.CheckStatus.UNKNOWN


def _reset_validation_state(scenario: Scenario) -> None:
    scenario.validation_requested = False
    scenario.validation_status = Scenario.ValidationStatus.IDLE
    scenario.last_validation_task_id = ""
    scenario.last_validation_at = None
    scenario.last_validation_data = {}
    scenario.check_status_actions = Scenario.CheckStatus.UNKNOWN
    scenario.check_status_node = Scenario.CheckStatus.UNKNOWN
    scenario.check_status_graph = Scenario.CheckStatus.UNKNOWN
    scenario.validation_trace = []
    scenario.validation_history = []
    scenario.save(
        update_fields=[
            "validation_requested",
            "validation_status",
            "last_validation_task_id",
            "last_validation_at",
            "last_validation_data",
            "check_status_actions",
            "check_status_node",
            "check_status_graph",
            "validation_trace",
            "validation_history",
            "updated_at",
        ]
    )


def _collect_field_updates(obj, new_values: dict) -> dict:
    changed_fields = {}
    for field_name, new_value in new_values.items():
        old_value = getattr(obj, field_name)
        if old_value != new_value:
            changed_fields[field_name] = {"old": old_value, "new": new_value}
    return changed_fields


def _extract_scenario_payload(payload: dict) -> tuple[str, str, str, dict, list, dict, list, list, list]:
    if not isinstance(payload, dict):
        raise ValidationError({"file": "Scenario YAML root must be an object."})

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValidationError({"metadata": "metadata must be an object when provided."})

    spec = payload.get("spec")
    if "spec" in payload and spec is not None and not isinstance(spec, dict):
        raise ValidationError({"spec": "spec must be an object when provided."})
    body = spec if isinstance(spec, dict) else payload
    if isinstance(body, dict) and "actionCatalog" in body:
        raise ValidationError(
            {"actionCatalog": "spec.actionCatalog is deprecated and no longer supported. Remove it from scenario YAML."}
        )

    name = metadata.get("name") or payload.get("name")
    if not name:
        raise ValidationError({"metadata": "metadata.name is required."})

    kind = payload.get("kind") or "Scenario"
    if kind != "Scenario":
        raise ValidationError({"kind": f"Expected kind 'Scenario', got '{kind}'."})
    description = metadata.get("description") or payload.get("description") or ""
    nodes = body.get("nodes") or []
    graph = body.get("graph") or {}
    runtime_env = body.get("runtimeEnv") or body.get("runtime_env") or []
    runtime_env_ref = body.get("runtimeEnvRef") or body.get("runtime_env_ref")
    if runtime_env_ref:
        runtime_env = [{"name": runtime_env_ref}]
    payloads = body.get("payloads") or []
    background_workloads = body.get("backgroundWorkloads") or body.get("background_workloads") or []

    if not isinstance(nodes, list):
        raise ValidationError({"nodes": "nodes must be a list."})
    if not isinstance(graph, dict):
        raise ValidationError({"graph": "graph must be an object."})
    if not isinstance(runtime_env, list):
        raise ValidationError({"runtimeEnv": "runtimeEnv must be a list."})
    if not isinstance(payloads, list):
        raise ValidationError({"payloads": "payloads must be a list."})
    if not isinstance(background_workloads, list):
        raise ValidationError({"backgroundWorkloads": "backgroundWorkloads must be a list."})
    if not nodes:
        raise ValidationError({"nodes": "nodes must include at least one node."})
    if not runtime_env:
        raise ValidationError({"runtimeEnv": "runtimeEnv (or runtimeEnvRef) is required."})

    return (
        name,
        kind,
        description,
        metadata,
        nodes,
        graph,
        runtime_env,
        payloads,
        background_workloads,
    )


def _validate_nodes_for_inventory(nodes: list, user) -> None:
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValidationError({"nodes": f"nodes[{index}] must be an object."})

        node_name = node.get("nodeName") or node.get("name")
        node_kind = (node.get("kind") or node.get("type") or "").lower()
        if not node_name:
            raise ValidationError({"nodes": f"nodes[{index}] requires nodeName (or name)."})
        if not node_kind:
            raise ValidationError({"nodes": f"nodes[{index}] requires kind (or type)."})

        if node_kind == "vm":
            if not VM.objects.filter(created_by=user, name=node_name).exists():
                raise ValidationError(
                    {"nodes": f"Inventory VM '{node_name}' referenced by nodes[{index}] was not found."}
                )


def _validate_graph(graph: dict, nodes: list) -> None:
    node_names = {
        node.get("nodeName") or node.get("name")
        for node in nodes
        if isinstance(node, dict)
    }
    edges = graph.get("edges") or []
    if not isinstance(edges, list):
        raise ValidationError({"graph": "graph.edges must be a list."})

    edge_names: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValidationError({"graph": f"graph.edges[{index}] must be an object."})
        edge_name = str(edge.get("name") or "").strip()
        if edge_name:
            if edge_name in edge_names:
                raise ValidationError({"graph": f"graph.edges[{index}].name '{edge_name}' is duplicated."})
            edge_names.add(edge_name)
        from_name = edge.get("from")
        to_name = edge.get("to")
        if not from_name or not to_name:
            raise ValidationError({"graph": f"graph.edges[{index}] requires from and to."})
        if from_name not in node_names:
            raise ValidationError({"graph": f"graph.edges[{index}].from '{from_name}' is not defined in nodes."})
        if to_name not in node_names:
            raise ValidationError({"graph": f"graph.edges[{index}].to '{to_name}' is not defined in nodes."})


def _validate_runtime_env_refs(runtime_env: list, user) -> None:
    for index, env_item in enumerate(runtime_env):
        if isinstance(env_item, str):
            env_name = env_item
        elif isinstance(env_item, dict):
            env_name = env_item.get("name")
        else:
            raise ValidationError({"runtimeEnv": f"runtimeEnv[{index}] must be a string or object."})
        if not env_name:
            raise ValidationError({"runtimeEnv": f"runtimeEnv[{index}] requires name."})
        if not RuntimeEnvironment.objects.filter(created_by=user, name=env_name).exists():
            raise ValidationError({"runtimeEnv": f"Runtime environment '{env_name}' was not found."})


class ScenarioListUploadAPIView(APIView):
    """List scenarios or upload one scenario YAML file."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = Scenario.objects.filter(created_by=request.user).order_by("name")
        return Response(ScenarioSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = ScenarioUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        uploaded_file = serializer.validated_data["file"]
        force = serializer.validated_data["force"]

        raw_bytes = uploaded_file.read()
        raw_yaml = raw_bytes.decode("utf-8")

        try:
            payload = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError as exc:
            raise ValidationError({"file": "Invalid YAML content."}) from exc

        (
            name,
            kind,
            description,
            metadata,
            nodes,
            graph,
            runtime_env,
            payloads,
            background_workloads,
        ) = _extract_scenario_payload(payload)

        _validate_nodes_for_inventory(nodes, user)
        _validate_graph(graph, nodes)
        _validate_runtime_env_refs(runtime_env, user)

        new_values = {
            "kind": kind,
            "description": description,
            "metadata": metadata,
            "nodes": nodes,
            "graph": graph,
            "runtime_env": runtime_env,
            "payloads": payloads,
            "background_workloads": background_workloads,
            "check_status_node": Scenario.CheckStatus.UNKNOWN,
            "check_status_graph": Scenario.CheckStatus.UNKNOWN,
            "check_status_actions": Scenario.CheckStatus.UNKNOWN,
            "validation_requested": False,
            "validation_status": Scenario.ValidationStatus.IDLE,
            "last_validation_task_id": "",
            "last_validation_at": None,
            "last_validation_data": {},
            "validation_trace": [],
            "validation_history": [],
            "raw_payload": payload,
            "raw_yaml": raw_yaml,
            "modified_by": user,
        }

        existing_scenario = Scenario.objects.filter(created_by=user, name=name).first()
        if existing_scenario:
            if not force:
                raise ValidationError(
                    {
                        "metadata": (
                            f"Scenario '{name}' already exists. "
                            "Use --force to overwrite during development."
                        )
                    }
                )
            changed_fields = _collect_field_updates(existing_scenario, {k: v for k, v in new_values.items() if k != "raw_yaml"})
            for field_name, value in new_values.items():
                setattr(existing_scenario, field_name, value)
            scenario_obj = existing_scenario
            scenario_obj.save()
            created_scenarios = []
            updated_scenarios = [{"name": name, "updated_fields": changed_fields}] if changed_fields else []
        else:
            scenario_obj = Scenario.objects.create(created_by=user, name=name, **new_values)
            created_scenarios = [{"name": scenario_obj.name, "kind": scenario_obj.kind}]
            updated_scenarios = []

        cached_name = f"{user.id}_{name}.yaml".replace("/", "_")
        scenario_obj.cached_yaml_file.save(cached_name, ContentFile(raw_bytes), save=True)

        return Response(
            {
                "message": "Scenario uploaded successfully.",
                "created": {"scenarios": created_scenarios},
                "updated": {"scenarios": updated_scenarios},
                "total": {"scenarios": Scenario.objects.filter(created_by=user).count()},
            },
            status=status.HTTP_200_OK,
        )


class ScenarioDetailAPIView(APIView):
    """Get or delete one scenario by name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_scenario(self, request, scenario_name: str) -> Scenario:
        return get_object_or_404(Scenario, created_by=request.user, name=scenario_name)

    def get(self, request, scenario_name: str, *args, **kwargs):
        _api_debug(
            "detail_get_received",
            user_id=request.user.id,
            scenario_name=scenario_name,
            query_params=dict(request.query_params),
        )
        scenario = self.get_scenario(request, scenario_name)
        _api_debug(
            "detail_get_resolved",
            scenario=scenario.name,
            validation_status=scenario.validation_status,
            validation_requested=scenario.validation_requested,
            check_status_actions=scenario.check_status_actions,
            check_status_node=scenario.check_status_node,
            check_status_graph=scenario.check_status_graph,
            history_size=len(scenario.validation_history or []),
            last_validation_at=str(scenario.last_validation_at or ""),
        )
        return Response(ScenarioSerializer(scenario).data, status=status.HTTP_200_OK)

    def delete(self, request, scenario_name: str, *args, **kwargs):
        scenario = self.get_scenario(request, scenario_name)
        scenario.delete()
        return Response({"message": f"Deleted scenario '{scenario_name}'."}, status=status.HTTP_200_OK)


def _get_scenario_spec(scenario: Scenario) -> dict:
    payload = scenario.raw_payload if isinstance(scenario.raw_payload, dict) else {}
    spec = payload.get("spec") if isinstance(payload, dict) else {}
    if not isinstance(spec, dict):
        return {}
    return spec


def _graph_summary_payload(scenario: Scenario, payload: dict | None = None) -> dict:
    if isinstance(payload, dict):
        return scenario.get_graph_check_summary(payload)
    if isinstance(scenario.last_validation_data, dict):
        return scenario.get_graph_check_summary(scenario.last_validation_data)
    return scenario.get_graph_check_summary({})


def _failure_report_payload(scenario: Scenario, payload: dict | None = None) -> dict:
    if isinstance(payload, dict):
        return scenario.build_failure_report(payload)
    if isinstance(scenario.last_validation_data, dict):
        return scenario.build_failure_report(scenario.last_validation_data)
    return scenario.build_failure_report({})


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


def _render_edge_placeholders(value, edge_context: dict[str, str]):
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
    if spec.get("phases") is not None:
        return [], ["spec.phases is not supported. Use spec.phaseTemplates with actions."]
    phases = spec.get("phaseTemplates")
    if phases is None:
        phases = []
    if not isinstance(phases, list):
        return [], ["spec.phaseTemplates must be a list."]

    errors: list[str] = []
    raw_nodes = spec.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []

    phase_candidates = [phase for phase in phases if isinstance(phase, dict)]
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
        raw_actions = _render_edge_placeholders(phase.get("actions"), edge_context)
        if raw_actions is None:
            normalized_actions = []
        elif not isinstance(raw_actions, list):
            errors.append(f"phaseTemplates[{phase_label}].actions must be a list.")
            normalized_actions = []
        else:
            normalized_actions = []
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
                normalized_actions.extend(expanded)
        valid_phases.append({**phase, "actions": normalized_actions})

    return valid_phases, errors


def _validate_actions(spec: dict, phase_name: str | None, edge_name: str | None = None) -> dict:
    errors: list[str] = []
    phase_templates, phase_errors = _extract_phase_templates(spec, phase_name, edge_name=edge_name)
    errors.extend(phase_errors)

    if "actionCatalog" in spec:
        errors.append("spec.actionCatalog is deprecated and no longer supported. Remove it from scenario YAML.")

    runtime_env_name = ""
    runtime_env_items = spec.get("runtimeEnv") or spec.get("runtime_env") or []
    if isinstance(runtime_env_items, list):
        for item in runtime_env_items:
            if isinstance(item, str) and item.strip():
                runtime_env_name = item.strip()
                break
            if isinstance(item, dict):
                candidate = str(item.get("name") or "").strip()
                if candidate:
                    runtime_env_name = candidate
                    break
    runtime_env_ref = str(spec.get("runtimeEnvRef") or spec.get("runtime_env_ref") or "").strip()
    if runtime_env_ref:
        runtime_env_name = runtime_env_ref
    preset_names: set[str] = set()
    runtime_env = RuntimeEnvironment.objects.filter(name=runtime_env_name).first() if runtime_env_name else None
    command_presets = runtime_env.command_preset if runtime_env and isinstance(runtime_env.command_preset, list) else []
    for preset in command_presets:
        if not isinstance(preset, dict):
            continue
        group = str(preset.get("group") or "").strip()
        name = str(preset.get("name") or "").strip()
        if name:
            preset_names.add(name)
        if group and name:
            preset_names.add(f"{group}/{name}")
    runtime_tags = runtime_env.tags if runtime_env and isinstance(runtime_env.tags, dict) else {}
    zenoh = runtime_tags.get("zenoh") if isinstance(runtime_tags, dict) else None
    if isinstance(zenoh, dict) and (bool(zenoh) or zenoh.get("enabled") is True):
        preset_names.update(builtin_zenoh_routing_preset_names())
    else:
        preset_names.add("routing/clean_router")

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

            if action_type == "collect_metrics":
                if action.get("target") in (None, ""):
                    errors.append(f"phase '{phase_label}' action[{idx}] collect_metrics requires field 'target'.")
                continue

            if action.get("target") in (None, "") and action_type != "wait":
                errors.append(f"phase '{phase_label}' action[{idx}] missing normalized target.")
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


def _resolve_vm_for_node(user, node: dict) -> VM | None:
    for candidate in _node_vm_candidates(node):
        vm = VM.objects.select_related("credential").filter(created_by=user, name=candidate).first()
        if vm:
            return vm
    return None


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


def _check_nodes_connectivity(
    user,
    scenario: Scenario,
    phase_name: str | None,
    *,
    edge_name: str | None = None,
) -> dict:
    nodes = scenario.nodes if isinstance(scenario.nodes, list) else []
    spec = _get_scenario_spec(scenario)
    required_targets = (
        _collect_target_nodes_for_phase(spec, phase_name, edge_name=edge_name)
        if phase_name
        else set()
    )

    results = []
    ok = True
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_name = str(node.get("nodeName") or node.get("name") or "")
        if required_targets and node_name not in required_targets:
            continue
        node_kind = str(node.get("kind") or node.get("type") or "").lower()
        if node_kind != "vm":
            continue

        vm = _resolve_vm_for_node(user, node)
        if vm is None:
            ok = False
            results.append({"node": node_name, "ok": False, "error": "VM not found in inventory."})
            continue
        if vm.credential is None:
            ok = False
            results.append({"node": node_name, "ok": False, "error": "VM has no SSH credential."})
            continue
        key_path = vm.credential.key_file.path if vm.credential.key_file else vm.credential.key_path
        if not key_path:
            ok = False
            results.append({"node": node_name, "ok": False, "error": "VM credential has no key path."})
            continue

        try:
            ssh = build_vm_ssh_client(
                {
                    "address": vm.address,
                    "user_name": vm.user_name,
                    "port": vm.port,
                    "key_path": key_path,
                }
            )
        except ValueError as exc:
            ok = False
            results.append({"node": node_name, "ok": False, "error": str(exc)})
            continue
        check = ssh.test_connectivity()
        results.append(
            {
                "node": node_name,
                "vm_name": vm.name,
                "ok": check.success,
                "returncode": check.returncode,
                "stdout": check.stdout,
                "stderr": check.stderr,
            }
        )
        ok = ok and check.success

    return {"ok": ok, "results": results}


def _find_node_by_name(scenario: Scenario, node_name: str) -> dict | None:
    nodes = scenario.nodes if isinstance(scenario.nodes, list) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        current_name = str(node.get("nodeName") or node.get("name") or "")
        if current_name == node_name:
            return node
    return None


def _check_graph_ping(user, scenario: Scenario) -> dict:
    graph = scenario.graph if isinstance(scenario.graph, dict) else {}
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    if not edges:
        return {"ok": True, "results": [], "checked_edges": 0}

    results: list[dict] = []
    overall_ok = True
    for idx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            results.append({"ok": False, "error": f"graph.edges[{idx}] must be an object."})
            overall_ok = False
            continue
        source_name = str(edge.get("from") or "").strip()
        target_name = str(edge.get("to") or "").strip()
        edge_name = str(edge.get("name") or f"edge-{idx + 1}").strip()
        if not source_name or not target_name:
            results.append(
                {
                    "ok": False,
                    "edge_name": edge_name,
                    "source_node": source_name,
                    "destination_node": target_name,
                    "error": "Edge requires non-empty from/to nodes.",
                }
            )
            overall_ok = False
            continue

        source_node = _find_node_by_name(scenario, source_name)
        dest_node = _find_node_by_name(scenario, target_name)
        if source_node is None or dest_node is None:
            results.append(
                {
                    "ok": False,
                    "edge_name": edge_name,
                    "source_node": source_name,
                    "destination_node": target_name,
                    "error": "Source or destination node was not found in scenario.nodes.",
                }
            )
            overall_ok = False
            continue

        source_vm = _resolve_vm_for_node(user, source_node)
        dest_vm = _resolve_vm_for_node(user, dest_node)
        if source_vm is None or dest_vm is None:
            results.append(
                {
                    "ok": False,
                    "edge_name": edge_name,
                    "source_node": source_name,
                    "destination_node": target_name,
                    "error": "Source or destination VM could not be resolved from inventory.",
                }
            )
            overall_ok = False
            continue
        if source_vm.credential is None:
            results.append(
                {
                    "ok": False,
                    "edge_name": edge_name,
                    "source_node": source_name,
                    "destination_node": target_name,
                    "error": f"Source VM '{source_vm.name}' has no SSH credential.",
                }
            )
            overall_ok = False
            continue

        source_key_path = (
            source_vm.credential.key_file.path
            if source_vm.credential.key_file
            else source_vm.credential.key_path
        )
        if not source_key_path:
            results.append(
                {
                    "ok": False,
                    "edge_name": edge_name,
                    "source_node": source_name,
                    "destination_node": target_name,
                    "error": f"Source VM '{source_vm.name}' credential has no key path.",
                }
            )
            overall_ok = False
            continue

        try:
            ssh = build_vm_ssh_client(
                {
                    "address": source_vm.address,
                    "user_name": source_vm.user_name,
                    "port": source_vm.port,
                    "key_path": source_key_path,
                }
            )
        except ValueError as exc:
            results.append(
                {
                    "ok": False,
                    "edge_name": edge_name,
                    "source_node": source_name,
                    "destination_node": target_name,
                    "error": str(exc),
                }
            )
            overall_ok = False
            continue

        command = f"ping -c 1 -W 2 {dest_vm.address}"
        ping_result = ssh.execute_command(command, timeout=10)
        results.append(
            {
                "ok": ping_result.success,
                "edge_name": edge_name,
                "source_node": source_name,
                "source_vm": source_vm.name,
                "destination_node": target_name,
                "destination_vm": dest_vm.name,
                "destination_address": dest_vm.address,
                "command": command,
                "returncode": ping_result.returncode,
                "stdout": ping_result.stdout,
                "stderr": ping_result.stderr,
            }
        )
        overall_ok = overall_ok and bool(ping_result.success)

    return {
        "ok": overall_ok,
        "checked_edges": len(results),
        "results": results,
    }


class ScenarioValidateAPIView(APIView):
    """Validate scenario action templates and optional node/graph connectivity."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, scenario_name: str, *args, **kwargs):
        _api_debug(
            "validate_post_received",
            user_id=request.user.id,
            scenario_name=scenario_name,
            payload=dict(request.data),
        )
        scenario = get_object_or_404(Scenario, created_by=request.user, name=scenario_name)
        clean = _parse_bool(request.data.get("clean"))
        if clean:
            active_runs = list(
                ScenarioValidationRun.objects.filter(
                    scenario=scenario,
                    status__in=[
                        ScenarioValidationRun.RunStatus.PENDING,
                        ScenarioValidationRun.RunStatus.RUNNING,
                        ScenarioValidationRun.RunStatus.CANCEL_REQUESTED,
                    ],
                ).order_by("-requested_at")
            )
            revoke_attempted = 0
            revoke_errors: list[dict] = []
            now_ts = timezone.now()
            for run in active_runs:
                run.cancel_requested = True
                run.status = ScenarioValidationRun.RunStatus.CANCEL_REQUESTED
                run.save(update_fields=["cancel_requested", "status"])
                if run.task_id:
                    try:
                        revoke_attempted += 1
                        validate_scenario_task.AsyncResult(run.task_id).revoke(terminate=False)
                    except Exception as exc:
                        revoke_errors.append({"run_id": run.id, "task_id": run.task_id, "error": str(exc)})
                run.status = ScenarioValidationRun.RunStatus.CANCELED
                run.canceled_by_clean = True
                run.finished_at = now_ts
                run.error_message = "Canceled by clean request."
                run.result_data = {
                    **(run.result_data if isinstance(run.result_data, dict) else {}),
                    "status": ScenarioValidationRun.RunStatus.CANCELED,
                    "cleaned_at": now_ts.isoformat(),
                }
                run.save(
                    update_fields=[
                        "status",
                        "canceled_by_clean",
                        "finished_at",
                        "error_message",
                        "result_data",
                    ]
                )
            _reset_validation_state(scenario)
            _api_debug(
                "validate_cleaned",
                scenario=scenario.name,
                revoked_runs=len(active_runs),
                revoke_attempted=revoke_attempted,
                revoke_errors=revoke_errors,
            )
            return Response(
                {
                    "scenario": scenario.name,
                    "cleaned": True,
                    "revoked_runs": len(active_runs),
                    "revoke_attempted": revoke_attempted,
                    "revoke_errors": revoke_errors,
                    "validation_status": scenario.validation_status,
                    "graph_edge_count": scenario.graph_edge_count(),
                    "graph_check_summary": _graph_summary_payload(scenario),
                    "failure_report": _failure_report_payload(scenario),
                },
                status=status.HTTP_200_OK,
            )
        check_actions = _parse_bool(request.data.get("check_actions"), default=True)
        check_nodes = _parse_bool(request.data.get("check_nodes"))
        check_graph = _parse_bool(request.data.get("check_graph"))
        if not check_actions and not check_nodes and not check_graph:
            check_actions = True
            check_nodes = True
            check_graph = True
        phase_name = request.data.get("phase_name")
        if phase_name is not None and not isinstance(phase_name, str):
            raise ValidationError({"phase_name": "phase_name must be a string when provided."})
        if isinstance(phase_name, str):
            phase_name = phase_name.strip() or None
        edge_name = request.data.get("edge_name")
        if edge_name is not None and not isinstance(edge_name, str):
            raise ValidationError({"edge_name": "edge_name must be a string when provided."})
        if isinstance(edge_name, str):
            edge_name = edge_name.strip() or None
        if edge_name and not check_graph:
            check_graph = True
            check_actions = False
            check_nodes = False
        if edge_name:
            graph_edges = scenario.graph_edges()
            if not any(str(item.get("name") or "") == edge_name for item in graph_edges):
                raise ValidationError({"edge_name": f"edge '{edge_name}' was not found in scenario.graph.edges."})

        now_ts = timezone.now()
        phase_key = phase_name or ""
        pending_graph_summary = scenario.get_graph_check_summary({"check_graph": check_graph})
        active_runs = ScenarioValidationRun.objects.filter(
            scenario=scenario,
            status__in=[
                ScenarioValidationRun.RunStatus.PENDING,
                ScenarioValidationRun.RunStatus.RUNNING,
                ScenarioValidationRun.RunStatus.CANCEL_REQUESTED,
            ],
            check_actions=check_actions,
            check_nodes=check_nodes,
            check_graph=check_graph,
            phase_name=phase_key,
        ).order_by("-requested_at")
        if edge_name:
            active_runs = [
                item
                for item in active_runs
                if str((item.result_data if isinstance(item.result_data, dict) else {}).get("edge_name") or "") == edge_name
            ]
            active_run = active_runs[0] if active_runs else None
        else:
            active_run = active_runs.first()
        if active_run is not None:
            recent = (now_ts - active_run.requested_at) <= timedelta(seconds=180)
            task_state = _task_state(active_run.task_id)
            inflight = task_state in {"PENDING", "RECEIVED", "STARTED", "RETRY", "PROGRESS"}
            if recent and inflight:
                _api_debug(
                    "validate_reuse_inflight",
                    scenario=scenario.name,
                    run_id=active_run.id,
                    task_id=active_run.task_id,
                    run_status=active_run.status,
                    inflight=inflight,
                    task_state=task_state,
                )
                progress = active_run.result_data if isinstance(active_run.result_data, dict) else {}
                return Response(
                    {
                        "scenario": scenario.name,
                        "phase_name": phase_name,
                        "validation_requested": True,
                        "validation_status": scenario.validation_status,
                        "execution_mode": "queued_existing",
                        "run_id": active_run.id,
                        "task_id": active_run.task_id,
                        "edge_name": edge_name,
                        "progress": progress,
                        "check_status_actions": scenario.check_status_actions,
                        "check_status_node": scenario.check_status_node,
                        "check_status_graph": scenario.check_status_graph,
                        "task_state": task_state,
                        "graph_edge_count": scenario.graph_edge_count(),
                        "graph_check_summary": _graph_summary_payload(scenario, progress),
                        "failure_report": _failure_report_payload(scenario, progress),
                    },
                    status=status.HTTP_200_OK,
                )

            active_run.status = ScenarioValidationRun.RunStatus.FAILED
            active_run.finished_at = now_ts
            if recent and not inflight:
                active_run.error_message = (
                    f"Released by new request; task was not inflight (state={task_state})."
                )
            else:
                active_run.error_message = "Marked stale and released by new request."
            active_run.result_data = {
                **(active_run.result_data if isinstance(active_run.result_data, dict) else {}),
                "status": ScenarioValidationRun.RunStatus.FAILED,
                "stale_task_released": True,
                "released_at": now_ts.isoformat(),
                "observed_task_state": task_state,
            }
            active_run.save(update_fields=["status", "finished_at", "error_message", "result_data"])
            _api_debug(
                "validate_stale_or_finished_task_released",
                scenario=scenario.name,
                run_id=active_run.id,
                task_id=active_run.task_id,
                inflight=inflight,
                recent=recent,
                task_state=task_state,
            )

        run = ScenarioValidationRun.objects.create(
            scenario=scenario,
            requested_by=request.user,
            check_actions=check_actions,
            check_nodes=check_nodes,
            check_graph=check_graph,
            phase_name=phase_key,
            status=ScenarioValidationRun.RunStatus.PENDING,
            result_data={
                "queued_at": now_ts.isoformat(),
                "phase_name": phase_name,
                "check_actions": check_actions,
                "check_nodes": check_nodes,
                "check_graph": check_graph,
                "edge_name": edge_name,
                "graph_check_summary": pending_graph_summary,
                "status": ScenarioValidationRun.RunStatus.PENDING,
            },
        )
        queue_failed = None
        task = None
        try:
            task = validate_scenario_task.apply_async(
                kwargs={
                    "user_id": request.user.id,
                    "scenario_id": scenario.id,
                    "run_id": run.id,
                    "check_actions": check_actions,
                    "check_nodes": check_nodes,
                    "check_graph": check_graph,
                    "phase_name": phase_name,
                    "edge_name": edge_name,
                },
                retry=False,
            )
        except Exception as exc:  # fallback when broker is down/unreachable
            queue_failed = str(exc)
            run.status = ScenarioValidationRun.RunStatus.FAILED
            run.finished_at = timezone.now()
            run.error_message = queue_failed
            run.result_data = {
                **(run.result_data if isinstance(run.result_data, dict) else {}),
                "status": ScenarioValidationRun.RunStatus.FAILED,
                "queue_error": queue_failed,
            }
            run.save(update_fields=["status", "finished_at", "error_message", "result_data"])
            _api_debug(
                "validate_queue_failed",
                scenario=scenario.name,
                run_id=run.id,
                error=queue_failed,
            )

        if queue_failed is not None:
            _api_debug(
                "validate_sync_fallback_start",
                scenario=scenario.name,
                check_actions=check_actions,
                check_nodes=check_nodes,
                check_graph=check_graph,
                phase_name=phase_name,
                edge_name=edge_name,
            )
            result = validate_scenario_task.run(
                user_id=request.user.id,
                scenario_id=scenario.id,
                run_id=run.id,
                check_actions=check_actions,
                check_nodes=check_nodes,
                check_graph=check_graph,
                phase_name=phase_name,
                edge_name=edge_name,
            )
            scenario.refresh_from_db()
            run.refresh_from_db()
            _api_debug(
                "validate_sync_fallback_done",
                scenario=scenario.name,
                ok=result.get("ok"),
                validation_status=scenario.validation_status,
            )
            return Response(
                {
                    "scenario": scenario.name,
                    "phase_name": phase_name,
                    "validation_requested": True,
                    "validation_status": scenario.validation_status,
                    "execution_mode": "sync_fallback",
                    "run_id": run.id,
                    "edge_name": edge_name,
                    "queue_error": queue_failed,
                    "result": result,
                    "graph_edge_count": scenario.graph_edge_count(),
                    "graph_check_summary": _graph_summary_payload(scenario, result),
                    "failure_report": _failure_report_payload(scenario, result),
                },
                status=status.HTTP_200_OK,
            )

        run.task_id = task.id or ""
        run.result_data = {
            **(run.result_data if isinstance(run.result_data, dict) else {}),
            "task_id": run.task_id,
        }
        run.save(update_fields=["task_id", "result_data"])

        scenario.validation_requested = True
        scenario.last_validation_task_id = run.task_id
        scenario.check_status_actions = _next_check_status(
            scenario.check_status_actions,
            check_actions,
            Scenario.CheckStatus.PENDING,
        )
        scenario.check_status_node = _next_check_status(
            scenario.check_status_node,
            check_nodes,
            Scenario.CheckStatus.PENDING,
        )
        scenario.check_status_graph = _next_check_status(
            scenario.check_status_graph,
            check_graph,
            Scenario.CheckStatus.PENDING,
        )
        scenario.validation_status = scenario.compute_validation_status()
        scenario.last_validation_data = {
            "queued_at": now_ts.isoformat(),
            "phase_name": phase_name,
            "edge_name": edge_name,
            "run_id": run.id,
            "check_actions": check_actions,
            "check_nodes": check_nodes,
            "check_graph": check_graph,
            "task_id": run.task_id,
            "status": Scenario.ValidationStatus.PENDING,
            "graph_check_summary": pending_graph_summary,
        }
        scenario.last_validation_data["failure_report"] = scenario.build_failure_report(
            scenario.last_validation_data
        )
        scenario.modified_by = request.user
        scenario.save(
            update_fields=[
                "validation_requested",
                "validation_status",
                "last_validation_task_id",
                "check_status_actions",
                "check_status_node",
                "check_status_graph",
                "last_validation_data",
                "modified_by",
                "updated_at",
            ]
        )
        _api_debug(
            "validate_queued",
            scenario=scenario.name,
            run_id=run.id,
            task_id=run.task_id,
            validation_status=scenario.validation_status,
            check_status_actions=scenario.check_status_actions,
            check_status_node=scenario.check_status_node,
            check_status_graph=scenario.check_status_graph,
        )
        return Response(
            {
                "scenario": scenario.name,
                "phase_name": phase_name,
                "edge_name": edge_name,
                "validation_requested": True,
                "validation_status": Scenario.ValidationStatus.PENDING,
                "run_id": run.id,
                "task_id": run.task_id,
                "graph_edge_count": scenario.graph_edge_count(),
                "graph_check_summary": _graph_summary_payload(scenario),
                "failure_report": _failure_report_payload(scenario),
            },
            status=status.HTTP_202_ACCEPTED,
        )

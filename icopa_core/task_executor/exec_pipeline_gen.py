"""Compile experiment plans into concrete generated execution payloads."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import itertools
import re
from typing import Any

import yaml

from .action_center import ActionCenterError, normalize_action_payload
from .targeting import TargetResolutionError, normalize_action_targets


_PLACEHOLDER_PATTERN = re.compile(r"\$\{(variant|edge)\.([A-Za-z_][A-Za-z0-9_]*)\}")
_EDGE_PLACEHOLDER_PATTERN = re.compile(r"\$\{edge\.[A-Za-z_][A-Za-z0-9_]*\}")


class ExecPipelineCompileError(ValueError):
    """Raised when an experiment plan cannot be compiled."""


def _as_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExecPipelineCompileError(f"{field_name} must be an object.")
    return value


def _as_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExecPipelineCompileError(f"{field_name} must be a list.")
    return value


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _spec(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    root = _as_dict(payload, field_name)
    spec = root.get("spec")
    if isinstance(spec, dict):
        return spec
    return root


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        return meta
    return {}


def _phase_actions_from_raw(
    *,
    raw_actions: Any,
    scenario_nodes: list[dict[str, Any]],
    error_field_path: str,
) -> list[dict[str, Any]]:
    if raw_actions is None:
        raw_actions = []
    items = _as_list(raw_actions, error_field_path)
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExecPipelineCompileError(f"{error_field_path}[{idx}] must be an object.")
        try:
            out.extend(
                normalize_action_payload(
                    raw_action=item,
                    nodes=scenario_nodes,
                    field_path=f"{error_field_path}[{idx}]",
                )
            )
        except ActionCenterError as exc:
            raise ExecPipelineCompileError(str(exc)) from exc
    return out


def _phase_actions_raw(
    *,
    raw_actions: Any,
    error_field_path: str,
) -> list[dict[str, Any]]:
    if raw_actions is None:
        raw_actions = []
    items = _as_list(raw_actions, error_field_path)
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExecPipelineCompileError(f"{error_field_path}[{idx}] must be an object.")
        out.append(deepcopy(item))
    return out


def _extract_phase_templates(scenario_spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    phase_templates = scenario_spec.get("phaseTemplates")
    if scenario_spec.get("phases") is not None:
        raise ExecPipelineCompileError("scenario.spec.phases is not supported. Use scenario.spec.phaseTemplates.")

    def _collect_from_list(raw_list: Any, *, list_field_path: str) -> None:
        for phase in _as_list(raw_list, list_field_path):
            if not isinstance(phase, dict):
                continue
            phase_name = str(phase.get("name") or "").strip()
            if not phase_name:
                continue
            if phase.get("steps") is not None:
                raise ExecPipelineCompileError(
                    f"scenario.spec.phaseTemplates[{phase_name}].steps is not supported. Use actions."
                )
            actions = _phase_actions_raw(
                raw_actions=phase.get("actions"),
                error_field_path=f"{phase_name}.actions",
            )
            templates[phase_name] = {
                "name": phase_name,
                "mode": str(phase.get("mode") or "sequential"),
                "actions": actions,
            }

    if phase_templates is not None:
        _collect_from_list(
            phase_templates,
            list_field_path="scenario.spec.phaseTemplates",
        )
        return templates

    return templates


def _scenario_nodes(scenario_spec: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = scenario_spec.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [item for item in nodes if isinstance(item, dict)]


def _scenario_node_names(scenario_spec: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for node in _scenario_nodes(scenario_spec):
        name = str(node.get("nodeName") or node.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _extract_named_graph_edges(scenario_spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    graph = scenario_spec.get("graph") or {}
    if graph and not isinstance(graph, dict):
        raise ExecPipelineCompileError("scenario.spec.graph must be an object.")
    edges_raw = graph.get("edges") if isinstance(graph, dict) else []
    if edges_raw is None:
        edges_raw = []
    if not isinstance(edges_raw, list):
        raise ExecPipelineCompileError("scenario.spec.graph.edges must be a list.")

    node_names = _scenario_node_names(scenario_spec)
    named_edges: list[dict[str, Any]] = []
    named_edge_by_name: dict[str, dict[str, Any]] = {}
    for idx, edge_raw in enumerate(edges_raw):
        if not isinstance(edge_raw, dict):
            raise ExecPipelineCompileError(f"scenario.spec.graph.edges[{idx}] must be an object.")
        from_name = str(edge_raw.get("from") or "").strip()
        to_name = str(edge_raw.get("to") or "").strip()
        if not from_name or not to_name:
            raise ExecPipelineCompileError(f"scenario.spec.graph.edges[{idx}] requires non-empty 'from' and 'to'.")
        if node_names:
            if from_name not in node_names:
                raise ExecPipelineCompileError(
                    f"scenario.spec.graph.edges[{idx}].from '{from_name}' is not defined in scenario.spec.nodes."
                )
            if to_name not in node_names:
                raise ExecPipelineCompileError(
                    f"scenario.spec.graph.edges[{idx}].to '{to_name}' is not defined in scenario.spec.nodes."
                )

        edge_name = str(edge_raw.get("name") or "").strip()
        if not edge_name:
            continue
        if edge_name in named_edge_by_name:
            raise ExecPipelineCompileError(
                f"scenario.spec.graph.edges contains duplicate edge name '{edge_name}'."
            )
        payload = {
            "name": edge_name,
            "from": from_name,
            "to": to_name,
            "type": deepcopy(edge_raw.get("type", "")),
            "link": deepcopy(edge_raw.get("link", [])),
        }
        named_edges.append(payload)
        named_edge_by_name[edge_name] = payload
    return named_edges, named_edge_by_name


def _contains_edge_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_edge_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_edge_placeholder(item) for item in value)
    if isinstance(value, str):
        return bool(_EDGE_PLACEHOLDER_PATTERN.search(value))
    return False


def _resolve_placeholder(
    *,
    scope: str,
    key: str,
    variant_values: dict[str, Any],
    edge_values: dict[str, Any] | None,
) -> Any:
    if scope == "variant":
        if key not in variant_values:
            raise ExecPipelineCompileError(f"Variant key '{key}' is not defined.")
        return deepcopy(variant_values[key])
    if edge_values is None:
        raise ExecPipelineCompileError(
            f"Edge key '{key}' is referenced but no edge context is available for this phase."
        )
    if key not in edge_values:
        raise ExecPipelineCompileError(f"Edge key '{key}' is not defined for selected edge '{edge_values.get('name')}'.")
    return deepcopy(edge_values[key])


def _render_template(
    value: Any,
    *,
    variant_values: dict[str, Any],
    edge_values: dict[str, Any] | None,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _render_template(item, variant_values=variant_values, edge_values=edge_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_render_template(item, variant_values=variant_values, edge_values=edge_values) for item in value]
    if not isinstance(value, str):
        return value

    if not _PLACEHOLDER_PATTERN.search(value):
        return value

    full_match = _PLACEHOLDER_PATTERN.fullmatch(value.strip())
    if full_match:
        scope = full_match.group(1)
        key = full_match.group(2)
        return _resolve_placeholder(
            scope=scope,
            key=key,
            variant_values=variant_values,
            edge_values=edge_values,
        )

    def repl(match: re.Match[str]) -> str:
        scope = match.group(1)
        key = match.group(2)
        rendered_value = _resolve_placeholder(
            scope=scope,
            key=key,
            variant_values=variant_values,
            edge_values=edge_values,
        )
        return str(rendered_value)

    return _PLACEHOLDER_PATTERN.sub(repl, value)


def _collect_axes(experiment_spec: dict[str, Any]) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    exploration = experiment_spec.get("exploration") or {}
    if not isinstance(exploration, dict):
        raise ExecPipelineCompileError("experiment.spec.exploration must be an object.")

    def _normalize_axis_map(raw_axes: dict[str, Any], *, field_path: str) -> dict[str, list[Any]]:
        normalized: dict[str, list[Any]] = {}
        for axis_name, values in raw_axes.items():
            axis = str(axis_name).strip()
            if not axis:
                raise ExecPipelineCompileError(f"{field_path} keys must be non-empty strings.")
            axis_values = values if isinstance(values, list) else [values]
            if not axis_values:
                raise ExecPipelineCompileError(f"Axis '{axis}' must include at least one value.")
            normalized[axis] = axis_values
        return normalized

    def _axes_from_exploration(raw_exploration: dict[str, Any], *, field_path: str) -> dict[str, list[Any]]:
        if "axes" in raw_exploration:
            raw_axes = raw_exploration.get("axes") or {}
            return _normalize_axis_map(
                _as_dict(raw_axes, f"{field_path}.axes"),
                field_path=f"{field_path}.axes",
            )
        raw_axes = {
            key: value
            for key, value in raw_exploration.items()
            if str(key) not in {"include", "exclude"}
        }
        return _normalize_axis_map(raw_axes, field_path=field_path)

    axes: dict[str, list[Any]] = {}
    axes.update(_axes_from_exploration(exploration, field_path="experiment.spec.exploration"))

    selections = experiment_spec.get("selections") or {}
    if selections and not isinstance(selections, dict):
        raise ExecPipelineCompileError("experiment.spec.selections must be an object.")
    for key, raw_value in selections.items():
        axis = str(key).strip()
        if not axis or axis in axes:
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        if not values:
            raise ExecPipelineCompileError(f"Selection axis '{axis}' must include at least one value.")
        axes[axis] = values

    return axes, exploration


def _extract_exec_edge_axes_by_edge(
    *,
    experiment_spec: dict[str, Any],
) -> dict[str, dict[str, list[Any]]]:
    raw_exec_edge = experiment_spec.get("execEdge")
    if raw_exec_edge is None:
        return {}

    exec_edge_list = _as_list(raw_exec_edge, "experiment.spec.execEdge")
    edge_axes_by_edge_name: dict[str, dict[str, list[Any]]] = {}

    for edge_group_idx, edge_group in enumerate(exec_edge_list):
        field_path = f"experiment.spec.execEdge[{edge_group_idx}]"
        if not isinstance(edge_group, dict):
            raise ExecPipelineCompileError(f"{field_path} must be an object.")
        inherited_edge_refs = edge_group.get("edgeRefs")
        if inherited_edge_refs is None:
            raise ExecPipelineCompileError(f"{field_path}.edgeRefs is required.")
        inherited_edge_refs_list = _as_list(inherited_edge_refs, f"{field_path}.edgeRefs")
        if not inherited_edge_refs_list:
            raise ExecPipelineCompileError(f"{field_path}.edgeRefs cannot be empty.")

        exploration = edge_group.get("exploration")
        if exploration is None:
            continue
        if not isinstance(exploration, dict):
            raise ExecPipelineCompileError(f"{field_path}.exploration must be an object when provided.")
        if "include" in exploration or "exclude" in exploration:
            raise ExecPipelineCompileError(
                f"{field_path}.exploration.include/exclude is not supported yet. Use axis mapping only."
            )

        if "axes" in exploration:
            raw_axes = _as_dict(exploration.get("axes") or {}, f"{field_path}.exploration.axes")
        else:
            raw_axes = _as_dict(exploration, f"{field_path}.exploration")

        normalized_axes: dict[str, list[Any]] = {}
        for axis_name, values in raw_axes.items():
            axis = str(axis_name).strip()
            if not axis:
                raise ExecPipelineCompileError(f"{field_path}.exploration keys must be non-empty strings.")
            axis_values = values if isinstance(values, list) else [values]
            if not axis_values:
                raise ExecPipelineCompileError(f"{field_path}.exploration axis '{axis}' must include at least one value.")
            normalized_axes[axis] = axis_values

        for edge_idx, edge_ref in enumerate(inherited_edge_refs_list):
            edge_name = str(edge_ref or "").strip()
            if not edge_name:
                raise ExecPipelineCompileError(f"{field_path}.edgeRefs[{edge_idx}] must be a non-empty string.")
            existing = edge_axes_by_edge_name.get(edge_name)
            if existing is not None and existing != normalized_axes:
                raise ExecPipelineCompileError(
                    f"{field_path}.exploration conflicts with another execEdge block for edge '{edge_name}'."
                )
            edge_axes_by_edge_name[edge_name] = deepcopy(normalized_axes)
    return edge_axes_by_edge_name


def _extract_experiment_phase_entries(experiment_spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw_phases = experiment_spec.get("phases")
    raw_exec_edge = experiment_spec.get("execEdge")
    if raw_phases is None and raw_exec_edge is None:
        if any(
            experiment_spec.get(field_name) is not None
            for field_name in ("phasesPrerun", "phasesInEachRun", "phases_pre_run", "phases_in_each_run")
        ):
            raise ExecPipelineCompileError(
                "experiment.spec.phasesPrerun/phasesInEachRun are not supported. Use experiment.spec.phases or experiment.spec.execEdge."
            )
        return []

    entries: list[dict[str, Any]] = []
    if raw_phases is not None:
        for idx, phase in enumerate(_as_list(raw_phases, "experiment.spec.phases")):
            entries.append(
                {
                    "phase": phase,
                    "field_path": f"experiment.spec.phases[{idx}]",
                }
            )

    if raw_exec_edge is not None:
        exec_edge_list = _as_list(raw_exec_edge, "experiment.spec.execEdge")
        for edge_group_idx, edge_group in enumerate(exec_edge_list):
            field_path = f"experiment.spec.execEdge[{edge_group_idx}]"
            if not isinstance(edge_group, dict):
                raise ExecPipelineCompileError(f"{field_path} must be an object.")
            inherited_edge_refs = edge_group.get("edgeRefs")
            if inherited_edge_refs is None:
                raise ExecPipelineCompileError(f"{field_path}.edgeRefs is required.")
            inherited_edge_refs_list = _as_list(inherited_edge_refs, f"{field_path}.edgeRefs")
            if not inherited_edge_refs_list:
                raise ExecPipelineCompileError(f"{field_path}.edgeRefs cannot be empty.")
            group_phases = edge_group.get("phases")
            if group_phases is None:
                raise ExecPipelineCompileError(f"{field_path}.phases is required.")
            for phase_idx, phase in enumerate(_as_list(group_phases, f"{field_path}.phases")):
                phase_path = f"{field_path}.phases[{phase_idx}]"
                phase_copy = deepcopy(phase) if isinstance(phase, dict) else phase
                if isinstance(phase_copy, dict) and "edgeRefs" not in phase_copy:
                    phase_copy["edgeRefs"] = deepcopy(inherited_edge_refs_list)
                entries.append(
                    {
                        "phase": phase_copy,
                        "field_path": phase_path,
                    }
                )
    return entries


def _normalize_phase_edge_refs(raw_phase: dict[str, Any], *, field_path: str) -> list[str] | None:
    if "edgeRefs" not in raw_phase:
        return None
    raw_edge_refs = raw_phase.get("edgeRefs")
    refs = _as_list(raw_edge_refs, f"{field_path}.edgeRefs")
    if not refs:
        raise ExecPipelineCompileError(f"{field_path}.edgeRefs cannot be empty.")
    normalized_refs: list[str] = []
    for edge_idx, edge_ref in enumerate(refs):
        name = str(edge_ref or "").strip()
        if not name:
            raise ExecPipelineCompileError(
                f"{field_path}.edgeRefs[{edge_idx}] must be a non-empty string."
            )
        if name not in normalized_refs:
            normalized_refs.append(name)
    return normalized_refs


def _extract_phase_specs(
    *,
    experiment_spec: dict[str, Any],
    scenario_templates: dict[str, dict[str, Any]],
    named_edge_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    phase_entries = _extract_experiment_phase_entries(experiment_spec)
    if not phase_entries:
        raise ExecPipelineCompileError("experiment.spec.phases/experiment.spec.execEdge cannot be empty.")

    phase_specs: list[dict[str, Any]] = []
    for idx, phase_entry in enumerate(phase_entries):
        raw_phase = phase_entry.get("phase")
        field_path = str(phase_entry.get("field_path") or f"experiment.spec.phases[{idx}]")
        if not isinstance(raw_phase, dict):
            raise ExecPipelineCompileError(f"{field_path} must be an object.")

        use_template = str(raw_phase.get("useTemplate") or "").strip()
        phase_name = str(raw_phase.get("name") or "").strip()
        phase_mode = str(raw_phase.get("mode") or "sequential").strip() or "sequential"

        template_actions: list[dict[str, Any]] = []
        template_mode = "sequential"
        source_template = ""
        if use_template:
            template = scenario_templates.get(use_template)
            if template is None:
                raise ExecPipelineCompileError(f"Phase template '{use_template}' was not found in scenario.")
            template_actions = deepcopy(template.get("actions") or [])
            template_mode = str(template.get("mode") or "sequential")
            source_template = use_template

        if raw_phase.get("steps") is not None:
            raise ExecPipelineCompileError(
                f"{field_path}.steps is not supported. Use actions."
            )

        explicit_actions_raw = raw_phase.get("actions")
        if explicit_actions_raw is not None:
            actions = _phase_actions_raw(
                raw_actions=explicit_actions_raw,
                error_field_path=f"{field_path}.actions",
            )
        else:
            actions = deepcopy(template_actions)

        if not actions:
            raise ExecPipelineCompileError(
                f"{field_path} resolves to empty action list."
            )

        edge_refs = _normalize_phase_edge_refs(raw_phase, field_path=field_path)
        if edge_refs is not None:
            unknown_refs = [item for item in edge_refs if item not in named_edge_by_name]
            if unknown_refs:
                raise ExecPipelineCompileError(
                    f"{field_path}.edgeRefs contains unknown edge names: {', '.join(unknown_refs)}."
                )

        uses_edge_placeholder = any(_contains_edge_placeholder(action) for action in actions)
        if uses_edge_placeholder and edge_refs is None:
            resolved_phase_name = phase_name or source_template or f"phase-{idx + 1}"
            raise ExecPipelineCompileError(
                f"phase '{resolved_phase_name}' references edge placeholders and requires {field_path}.edgeRefs."
            )

        phase_specs.append(
            {
                "name": phase_name or source_template or f"phase-{idx + 1}",
                "mode": phase_mode or template_mode or "sequential",
                "source_template": source_template,
                "actions": actions,
                "edge_refs": edge_refs,
                "field_path": field_path,
            }
        )
    return phase_specs


def _scenario_runtime_env_name(scenario_payload: dict[str, Any]) -> str:
    scenario_spec = _spec(scenario_payload, "scenario_payload")
    runtime_env = scenario_spec.get("runtimeEnv") or scenario_spec.get("runtime_env") or []
    if not isinstance(runtime_env, list):
        return ""
    for item in runtime_env:
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                return name
    runtime_env_ref = scenario_spec.get("runtimeEnvRef") or scenario_spec.get("runtime_env_ref")
    if isinstance(runtime_env_ref, str) and runtime_env_ref.strip():
        return runtime_env_ref.strip()
    return ""


def _resolve_runtime_env_name(
    *,
    variant_values: dict[str, Any],
    experiment_spec: dict[str, Any],
    scenario_payload: dict[str, Any],
) -> str:
    for key in ("runtimeEnv", "runtime_env", "runtime", "runtimeEnvRef"):
        value = variant_values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    selections = experiment_spec.get("selections") or {}
    if isinstance(selections, dict):
        for key in ("runtimeEnv", "runtime_env", "runtime", "runtimeEnvRef"):
            value = selections.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                candidate = value[0]
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return _scenario_runtime_env_name(scenario_payload)


def _matches_partial_filter(variant: dict[str, Any], matcher: dict[str, Any]) -> bool:
    for key, value in matcher.items():
        if variant.get(str(key)) != value:
            return False
    return True


def _expand_variants(axes: dict[str, list[Any]], exploration: dict[str, Any]) -> list[dict[str, Any]]:
    if not axes:
        variants = [{}]
    else:
        axis_names = sorted(axes.keys())
        axis_values = [axes[name] for name in axis_names]
        variants = [
            {name: combo[idx] for idx, name in enumerate(axis_names)}
            for combo in itertools.product(*axis_values)
        ]

    include_filters = exploration.get("include") or []
    exclude_filters = exploration.get("exclude") or []

    if include_filters:
        include_filter_list = [item for item in _as_list(include_filters, "experiment.spec.exploration.include") if isinstance(item, dict)]
        variants = [
            item
            for item in variants
            if any(_matches_partial_filter(item, filter_item) for filter_item in include_filter_list)
        ]

    if exclude_filters:
        exclude_filter_list = [item for item in _as_list(exclude_filters, "experiment.spec.exploration.exclude") if isinstance(item, dict)]
        variants = [
            item
            for item in variants
            if not any(_matches_partial_filter(item, filter_item) for filter_item in exclude_filter_list)
        ]

    return variants


def _materialize_phases(
    *,
    phase_specs: list[dict[str, Any]],
    scenario_nodes: list[dict[str, Any]],
    variant_values: dict[str, Any],
    edge_values: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    compiled_phases: list[dict[str, Any]] = []
    for phase_spec in phase_specs:
        edge_refs = phase_spec.get("edge_refs")
        if isinstance(edge_refs, list) and edge_refs:
            current_edge_name = str((edge_values or {}).get("name") or "").strip()
            if not current_edge_name or current_edge_name not in edge_refs:
                continue

        resolved_phase_name = str(phase_spec.get("name") or "").strip()
        resolved_phase_mode = str(phase_spec.get("mode") or "sequential").strip() or "sequential"
        source_template = str(phase_spec.get("source_template") or "").strip()
        actions = phase_spec.get("actions") if isinstance(phase_spec.get("actions"), list) else []
        phase_field_path = str(phase_spec.get("field_path") or f"phase[{resolved_phase_name}]")

        rendered_actions = []
        for action_idx, action_raw in enumerate(actions):
            rendered = _render_template(
                action_raw,
                variant_values=variant_values,
                edge_values=edge_values,
            )
            normalized = _as_dict(rendered, f"phase[{resolved_phase_name}].actions[{action_idx}]")

            normalized_actions = _phase_actions_from_raw(
                raw_actions=[normalized],
                scenario_nodes=scenario_nodes,
                error_field_path=f"{phase_field_path}.actions",
            )
            if not normalized_actions:
                raise ExecPipelineCompileError(
                    f"phase '{resolved_phase_name}' action[{action_idx}] resolves to empty action payload."
                )
            try:
                expanded: list[dict[str, Any]] = []
                for normalized_action in normalized_actions:
                    expanded.extend(
                        normalize_action_targets(
                            action=normalized_action,
                            nodes=scenario_nodes,
                        )
                    )
            except TargetResolutionError as exc:
                raise ExecPipelineCompileError(
                    f"phase '{resolved_phase_name}' action[{action_idx}] has invalid target: {exc}"
                ) from exc
            rendered_actions.extend(expanded)

        if not rendered_actions:
            raise ExecPipelineCompileError(
                f"phase '{resolved_phase_name}' resolves to empty action list for edge context."
            )

        compiled_phases.append(
            {
                "name": resolved_phase_name,
                "mode": resolved_phase_mode,
                "source_template": source_template,
                "actions": rendered_actions,
            }
        )
    return compiled_phases


def _selected_run_edges(
    *,
    phase_specs: list[dict[str, Any]],
    named_edges: list[dict[str, Any]],
    named_edge_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any] | None]:
    referenced_names: list[str] = []
    for phase in phase_specs:
        edge_refs = phase.get("edge_refs")
        if not isinstance(edge_refs, list):
            continue
        for edge_name in edge_refs:
            name = str(edge_name or "").strip()
            if name and name not in referenced_names:
                referenced_names.append(name)

    if not referenced_names:
        return [None]

    if not named_edges:
        raise ExecPipelineCompileError(
            "experiment.spec.phases/experiment.spec.execEdge references edgeRefs, but scenario.spec.graph.edges does not define named edges."
        )

    selected: list[dict[str, Any]] = []
    for edge in named_edges:
        edge_name = str(edge.get("name") or "").strip()
        if edge_name in referenced_names:
            selected.append(deepcopy(edge))

    missing = [name for name in referenced_names if name not in named_edge_by_name]
    if missing:
        raise ExecPipelineCompileError(
            f"experiment.spec.phases/experiment.spec.execEdge edgeRefs contains unknown edge names: {', '.join(missing)}."
        )
    if not selected:
        raise ExecPipelineCompileError(
            "No matching scenario edges were selected by experiment.spec.phases.edgeRefs."
        )
    return selected


def compile_experiment_plan(
    *,
    generated_name: str,
    experiment_payload: dict[str, Any],
    scenario_payload: dict[str, Any],
    max_total_runs: int | None = None,
) -> dict[str, Any]:
    """Compile one experiment into immutable generated runs payload."""
    generated_name = str(generated_name or "").strip()
    if not generated_name:
        raise ExecPipelineCompileError("generated_name is required.")
    if max_total_runs is not None and max_total_runs <= 0:
        raise ExecPipelineCompileError("max_total_runs must be > 0 when provided.")

    experiment_spec = _spec(experiment_payload, "experiment_payload")
    scenario_spec = _spec(scenario_payload, "scenario_payload")
    execution = experiment_spec.get("execution") or {}
    if execution and not isinstance(execution, dict):
        raise ExecPipelineCompileError("experiment.spec.execution must be an object.")

    if execution.get("runs") not in (None, ""):
        raise ExecPipelineCompileError(
            "experiment.spec.execution.runs is not supported. Each variant combination runs exactly once."
        )
    stop_on_failure = _as_bool(execution.get("stopOnFailure"), default=True)
    connectivity_precheck = _as_bool(execution.get("connectivityPrecheck"), default=True)

    global_axes, exploration = _collect_axes(experiment_spec)

    scenario_nodes = _scenario_nodes(scenario_spec)
    scenario_templates = _extract_phase_templates(scenario_spec)
    if not scenario_templates:
        raise ExecPipelineCompileError("Scenario does not define any phase templates.")

    named_edges, named_edge_by_name = _extract_named_graph_edges(scenario_spec)
    phase_specs = _extract_phase_specs(
        experiment_spec=experiment_spec,
        scenario_templates=scenario_templates,
        named_edge_by_name=named_edge_by_name,
    )
    run_edges = _selected_run_edges(
        phase_specs=phase_specs,
        named_edges=named_edges,
        named_edge_by_name=named_edge_by_name,
    )
    edge_axes_by_edge_name = _extract_exec_edge_axes_by_edge(experiment_spec=experiment_spec)

    runs: list[dict[str, Any]] = []
    run_sequence = 1
    for edge_values in run_edges:
        edge_name = str(edge_values.get("name") or "").strip() if isinstance(edge_values, dict) else ""
        edge_axes = edge_axes_by_edge_name.get(edge_name) if edge_name else None
        merged_axes = deepcopy(global_axes)
        if isinstance(edge_axes, dict):
            merged_axes.update(deepcopy(edge_axes))

        variants = _expand_variants(merged_axes, exploration)
        if not variants:
            edge_desc = edge_name or "global"
            raise ExecPipelineCompileError(
                f"No variant matched the configured exploration constraints for edge '{edge_desc}'."
            )

        for variant_index, variant_values in enumerate(variants, start=1):
            phases = _materialize_phases(
                phase_specs=phase_specs,
                scenario_nodes=scenario_nodes,
                variant_values=variant_values,
                edge_values=edge_values if isinstance(edge_values, dict) else None,
            )
            if not phases:
                raise ExecPipelineCompileError(
                    "Generated run has no phases after applying edgeRefs filters."
                )
            runs.append(
                {
                    "run_id": f"run-{run_sequence:03d}",
                    "variant_index": variant_index,
                    "runtime_env_name": _resolve_runtime_env_name(
                        variant_values=variant_values,
                        experiment_spec=experiment_spec,
                        scenario_payload=scenario_payload,
                    ),
                    "variant_values": deepcopy(variant_values),
                    "edge": deepcopy(edge_values) if isinstance(edge_values, dict) else {},
                    "phases": phases,
                }
            )
            run_sequence += 1

    total_runs = len(runs)
    if total_runs < 1:
        raise ExecPipelineCompileError(
            f"Generated run count must be >= 1, got {total_runs}."
        )
    if max_total_runs is not None and total_runs > max_total_runs:
        raise ExecPipelineCompileError(
            f"Generated run count must be <= {max_total_runs}, got {total_runs}."
        )

    experiment_meta = _metadata(experiment_payload)
    scenario_meta = _metadata(scenario_payload)
    source_experiment_name = str(experiment_meta.get("name") or experiment_payload.get("name") or "").strip()
    source_scenario_name = str(
        experiment_meta.get("scenarioRef")
        or experiment_spec.get("scenarioRef")
        or scenario_meta.get("name")
        or scenario_payload.get("name")
        or ""
    ).strip()

    return {
        "kind": "GeneratedExperimentPlan",
        "metadata": {
            "name": generated_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "source_refs": {
            "experiment_name": source_experiment_name,
            "scenario_name": source_scenario_name,
        },
        "execution_policy": {
            "stop_on_failure": stop_on_failure,
            "connectivity_precheck": connectivity_precheck,
        },
        "total_generated_runs": total_runs,
        "runs": runs,
        "source_snapshots": {
            "experiment": deepcopy(experiment_payload),
            "scenario": deepcopy(scenario_payload),
        },
    }


def render_generated_plan_yaml(compiled_plan: dict[str, Any]) -> str:
    """Render compiled payload as YAML for export."""
    return yaml.safe_dump(compiled_plan, sort_keys=False)


class ExecPipelineGen:
    """Legacy wrapper around the compile helpers."""

    def compile(
        self,
        *,
        generated_name: str,
        experiment_payload: dict[str, Any],
        scenario_payload: dict[str, Any],
        max_total_runs: int | None = None,
    ) -> dict[str, Any]:
        return compile_experiment_plan(
            generated_name=generated_name,
            experiment_payload=experiment_payload,
            scenario_payload=scenario_payload,
            max_total_runs=max_total_runs,
        )

    def export_yaml(self, compiled_plan: dict[str, Any]) -> str:
        return render_generated_plan_yaml(compiled_plan)

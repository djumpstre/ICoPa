"""Centralized canonical action catalog and strict action normalization."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .targeting import TargetResolutionError, parse_action_target_ref, resolve_target_ref_to_vm_nodes

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "configs" / "actions" / "action_center.yaml"
_ALLOWED_ACTION_KEYS = {"type", "targetRef", "targetRefs", "preset", "parameters"}
_LEGACY_ACTION_KEYS = {"action", "execAction", "target", "targets"}
_TARGET_MODES = {"none", "single", "multi"}
_SCOPES = {"vm", "cluster"}


class ActionCenterError(ValueError):
    """Raised when action catalog or action payload is invalid."""


def _as_non_empty_str(value: Any, *, field_path: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ActionCenterError(f"{field_path} must be a non-empty string.")
    return text


def _as_list(value: Any, *, field_path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ActionCenterError(f"{field_path} must be a list.")
    return value


def _as_dict(value: Any, *, field_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ActionCenterError(f"{field_path} must be an object.")
    return value


@lru_cache(maxsize=1)
def _catalog_data() -> dict[str, Any]:
    if not _CATALOG_PATH.exists():
        raise ActionCenterError(f"Action catalog file not found: {_CATALOG_PATH}")
    with _CATALOG_PATH.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    payload = _as_dict(raw, field_path=str(_CATALOG_PATH))
    actions = _as_list(payload.get("actions"), field_path=f"{_CATALOG_PATH}.actions")

    entries: list[dict[str, Any]] = []
    by_type: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(actions):
        item_dict = _as_dict(item, field_path=f"{_CATALOG_PATH}.actions[{idx}]")
        action_type = _as_non_empty_str(item_dict.get("type"), field_path=f"{_CATALOG_PATH}.actions[{idx}].type")
        scope = _as_non_empty_str(item_dict.get("scope"), field_path=f"{_CATALOG_PATH}.actions[{idx}].scope")
        if scope not in _SCOPES:
            raise ActionCenterError(
                f"{_CATALOG_PATH}.actions[{idx}].scope must be one of: {', '.join(sorted(_SCOPES))}."
            )
        target_mode = _as_non_empty_str(
            item_dict.get("target_mode"),
            field_path=f"{_CATALOG_PATH}.actions[{idx}].target_mode",
        )
        if target_mode not in _TARGET_MODES:
            raise ActionCenterError(
                f"{_CATALOG_PATH}.actions[{idx}].target_mode must be one of: {', '.join(sorted(_TARGET_MODES))}."
            )

        required_parameters_raw = item_dict.get("required_parameters", [])
        required_parameters = _as_list(
            required_parameters_raw,
            field_path=f"{_CATALOG_PATH}.actions[{idx}].required_parameters",
        )
        required_parameters_out: list[str] = []
        for param_idx, param in enumerate(required_parameters):
            required_parameters_out.append(
                _as_non_empty_str(
                    param,
                    field_path=f"{_CATALOG_PATH}.actions[{idx}].required_parameters[{param_idx}]",
                )
            )

        if action_type in by_type:
            raise ActionCenterError(f"Duplicate action type in catalog: '{action_type}'.")
        normalized = {
            "type": action_type,
            "scope": scope,
            "description": str(item_dict.get("description") or ""),
            "target_mode": target_mode,
            "required_parameters": required_parameters_out,
        }
        entries.append(normalized)
        by_type[action_type] = normalized

    if not by_type:
        raise ActionCenterError(f"{_CATALOG_PATH}.actions cannot be empty.")
    return {"entries": entries, "by_type": by_type}


def action_catalog_entries() -> list[dict[str, Any]]:
    """Return canonical action catalog entries."""
    return [dict(item) for item in _catalog_data()["entries"]]


def supported_action_types() -> set[str]:
    """Return supported canonical action type names."""
    return set(_catalog_data()["by_type"].keys())


def validate_action_type(action_type: str, *, field_path: str) -> dict[str, Any]:
    """Validate and return one canonical action catalog entry."""
    normalized = _as_non_empty_str(action_type, field_path=field_path)
    entry = _catalog_data()["by_type"].get(normalized)
    if entry is None:
        allowed = ", ".join(sorted(supported_action_types()))
        raise ActionCenterError(f"{field_path} has unsupported action type '{normalized}'. Allowed: {allowed}.")
    return dict(entry)


def _validate_parameters(parameters: Any, *, field_path: str) -> dict[str, Any]:
    if parameters in (None, ""):
        return {}
    if not isinstance(parameters, dict):
        raise ActionCenterError(f"{field_path} must be an object when provided.")
    return dict(parameters)


def _enforce_required_parameters(
    *,
    action_entry: dict[str, Any],
    parameters: dict[str, Any],
    field_path: str,
) -> None:
    for required_name in action_entry.get("required_parameters", []):
        if parameters.get(required_name) in (None, ""):
            raise ActionCenterError(
                f"{field_path}.parameters.{required_name} is required for action type '{action_entry['type']}'."
            )
    if action_entry["type"] == "wait":
        raw_seconds = parameters.get("seconds")
        try:
            seconds = float(raw_seconds)
        except (TypeError, ValueError) as exc:
            raise ActionCenterError(f"{field_path}.parameters.seconds must be numeric.") from exc
        if seconds < 0:
            raise ActionCenterError(f"{field_path}.parameters.seconds must be >= 0.")


def _normalize_single_target_action(
    *,
    action: dict[str, Any],
    nodes: list[dict[str, Any]],
    field_path: str,
) -> list[dict[str, Any]]:
    if action.get("targetRefs") not in (None, ""):
        raise ActionCenterError(f"{field_path}.targetRefs is not allowed for single-target actions.")
    if action.get("targetRef") in (None, ""):
        raise ActionCenterError(f"{field_path}.targetRef is required.")

    try:
        target_ref = parse_action_target_ref({"targetRef": action.get("targetRef")})
    except TargetResolutionError as exc:
        raise ActionCenterError(f"{field_path}.targetRef is invalid: {exc}") from exc
    if target_ref is None:
        raise ActionCenterError(f"{field_path}.targetRef is required.")

    try:
        resolved_nodes = resolve_target_ref_to_vm_nodes(target_ref=target_ref, nodes=nodes)
    except TargetResolutionError as exc:
        raise ActionCenterError(f"{field_path}.targetRef cannot be resolved: {exc}") from exc

    expanded: list[dict[str, Any]] = []
    for node_name in resolved_nodes:
        normalized = {
            "type": action["type"],
            "targetRef": {"kind": "vm", "name": node_name},
            "target": node_name,
            "parameters": dict(action["parameters"]),
        }
        if action.get("preset"):
            normalized["preset"] = str(action["preset"])
        expanded.append(normalized)
    return expanded


def _normalize_multi_target_action(
    *,
    action: dict[str, Any],
    nodes: list[dict[str, Any]],
    field_path: str,
) -> list[dict[str, Any]]:
    if action.get("targetRef") not in (None, ""):
        raise ActionCenterError(f"{field_path}.targetRef is not allowed for multi-target actions.")

    raw_target_refs = action.get("targetRefs")
    if not isinstance(raw_target_refs, list) or not raw_target_refs:
        raise ActionCenterError(f"{field_path}.targetRefs must be a non-empty list.")

    resolved_nodes: list[str] = []
    for idx, raw_ref in enumerate(raw_target_refs):
        try:
            target_ref = parse_action_target_ref({"targetRef": raw_ref})
        except TargetResolutionError as exc:
            raise ActionCenterError(f"{field_path}.targetRefs[{idx}] is invalid: {exc}") from exc
        if target_ref is None:
            raise ActionCenterError(f"{field_path}.targetRefs[{idx}] is required.")
        try:
            resolved_nodes.extend(resolve_target_ref_to_vm_nodes(target_ref=target_ref, nodes=nodes))
        except TargetResolutionError as exc:
            raise ActionCenterError(f"{field_path}.targetRefs[{idx}] cannot be resolved: {exc}") from exc

    deduped_nodes = sorted({item for item in resolved_nodes if item})
    if not deduped_nodes:
        raise ActionCenterError(f"{field_path}.targetRefs did not resolve to any VM target.")

    normalized = {
        "type": action["type"],
        "targetRefs": [{"kind": "vm", "name": node_name} for node_name in deduped_nodes],
        "targets": deduped_nodes,
        "parameters": dict(action["parameters"]),
    }
    return [normalized]


def normalize_action_payload(
    *,
    raw_action: dict[str, Any],
    nodes: list[dict[str, Any]],
    field_path: str,
) -> list[dict[str, Any]]:
    """Validate and normalize one canonical action payload into executable actions."""
    if not isinstance(raw_action, dict):
        raise ActionCenterError(f"{field_path} must be an object.")

    for legacy_key in sorted(_LEGACY_ACTION_KEYS):
        if raw_action.get(legacy_key) not in (None, ""):
            raise ActionCenterError(
                f"{field_path}.{legacy_key} is not supported. Use canonical fields: type, targetRef/targetRefs, "
                "preset, parameters."
            )

    unknown_keys = sorted(key for key in raw_action.keys() if key not in _ALLOWED_ACTION_KEYS)
    if unknown_keys:
        first = unknown_keys[0]
        raise ActionCenterError(
            f"{field_path}.{first} is not allowed. Action keys are limited to: {', '.join(sorted(_ALLOWED_ACTION_KEYS))}."
        )

    action_type = _as_non_empty_str(raw_action.get("type"), field_path=f"{field_path}.type")
    action_entry = validate_action_type(action_type, field_path=f"{field_path}.type")
    parameters = _validate_parameters(raw_action.get("parameters"), field_path=f"{field_path}.parameters")
    _enforce_required_parameters(action_entry=action_entry, parameters=parameters, field_path=field_path)

    preset = str(raw_action.get("preset") or "").strip()
    if action_type == "run_runtime_preset":
        if not preset:
            raise ActionCenterError(f"{field_path}.preset is required for action type 'run_runtime_preset'.")
    elif preset:
        raise ActionCenterError(f"{field_path}.preset is only allowed for action type 'run_runtime_preset'.")

    normalized_seed = {
        "type": action_type,
        "parameters": parameters,
    }
    if preset:
        normalized_seed["preset"] = preset
    if "targetRef" in raw_action:
        normalized_seed["targetRef"] = raw_action.get("targetRef")
    if "targetRefs" in raw_action:
        normalized_seed["targetRefs"] = raw_action.get("targetRefs")

    target_mode = str(action_entry["target_mode"])
    if target_mode == "none":
        if normalized_seed.get("targetRef") not in (None, "") or normalized_seed.get("targetRefs") not in (None, ""):
            raise ActionCenterError(f"{field_path} does not allow targetRef/targetRefs for action type '{action_type}'.")
        return [{"type": action_type, "parameters": parameters, **({"preset": preset} if preset else {})}]
    if target_mode == "single":
        return _normalize_single_target_action(action=normalized_seed, nodes=nodes, field_path=field_path)
    if target_mode == "multi":
        return _normalize_multi_target_action(action=normalized_seed, nodes=nodes, field_path=field_path)

    raise ActionCenterError(f"{field_path}.type '{action_type}' has unsupported target mode '{target_mode}'.")

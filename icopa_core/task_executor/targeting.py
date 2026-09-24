"""Target normalization and resolution helpers for scenario/experiment actions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class TargetResolutionError(ValueError):
    """Raised when an action target cannot be parsed or resolved."""


def _node_name(node: dict[str, Any]) -> str:
    return str(node.get("nodeName") or node.get("name") or "").strip()


def _node_kind(node: dict[str, Any]) -> str:
    return str(node.get("kind") or node.get("type") or "").strip().lower()


def _target_ref_from_string(raw_target: str) -> dict[str, str]:
    text = str(raw_target or "").strip()
    if not text:
        raise TargetResolutionError("Action target cannot be empty.")
    delimiter_index = -1
    for delimiter in ("/", ":"):
        candidate_index = text.find(delimiter)
        if candidate_index > 0 and (delimiter_index < 0 or candidate_index < delimiter_index):
            delimiter_index = candidate_index

    if delimiter_index < 0:
        return {"kind": "vm", "name": text}

    kind = text[:delimiter_index]
    name = text[delimiter_index + 1 :]
    kind_text = str(kind).strip().lower()
    name_text = str(name).strip()
    if not kind_text or not name_text:
        raise TargetResolutionError(
            "Action target must use '<kind>/<name>' or '<kind>:<name>' when namespaced, e.g. 'vm/vm_home'."
        )
    return {"kind": kind_text, "name": name_text}


def parse_action_target_ref(action: dict[str, Any]) -> dict[str, str] | None:
    """Resolve one canonical targetRef from action target fields."""
    if not isinstance(action, dict):
        raise TargetResolutionError("Action must be an object.")

    raw_target_ref = action.get("targetRef")
    if raw_target_ref not in (None, ""):
        if isinstance(raw_target_ref, dict):
            kind = str(raw_target_ref.get("kind") or "").strip().lower()
            name = str(raw_target_ref.get("name") or "").strip()
            if not kind or not name:
                raise TargetResolutionError("Action field 'targetRef' requires non-empty 'kind' and 'name'.")
            ref = {"kind": kind, "name": name}
        else:
            ref = _target_ref_from_string(str(raw_target_ref))
    else:
        raw_target = action.get("target")
        if raw_target in (None, ""):
            return None
        ref = _target_ref_from_string(str(raw_target))

    if ref["kind"] == "cluster":
        raise TargetResolutionError("Target kind 'cluster' is reserved and not implemented yet.")
    if ref["kind"] not in {"vm", "site"}:
        raise TargetResolutionError(
            f"Unsupported target kind '{ref['kind']}'. Supported kinds: vm, site."
        )
    return ref


def resolve_target_ref_to_vm_nodes(
    *,
    target_ref: dict[str, str],
    nodes: list[dict[str, Any]],
) -> list[str]:
    """Resolve one targetRef to concrete VM node names."""
    kind = str(target_ref.get("kind") or "").strip().lower()
    name = str(target_ref.get("name") or "").strip()
    if not kind or not name:
        raise TargetResolutionError("target_ref requires non-empty 'kind' and 'name'.")

    vm_nodes = [
        node
        for node in nodes
        if isinstance(node, dict) and _node_name(node) and _node_kind(node) == "vm"
    ]
    if kind == "vm":
        for node in vm_nodes:
            node_name = _node_name(node)
            if node_name == name:
                return [node_name]
        raise TargetResolutionError(f"VM target '{name}' was not found in scenario nodes.")

    # kind == "site"
    matched = []
    for node in vm_nodes:
        labels = node.get("labels")
        if not isinstance(labels, dict):
            continue
        site = str(labels.get("site") or "").strip()
        if site == name:
            matched.append(_node_name(node))
    matched = sorted({item for item in matched if item})
    if not matched:
        raise TargetResolutionError(f"Site target '{name}' did not match any VM node by labels.site.")
    return matched


def normalize_action_targets(
    *,
    action: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return concrete action payloads with resolved VM node targets."""
    if not isinstance(action, dict):
        raise TargetResolutionError("Action must be an object.")

    normalized = deepcopy(action)
    target_ref = parse_action_target_ref(normalized)
    if target_ref is not None:
        resolved_nodes = resolve_target_ref_to_vm_nodes(target_ref=target_ref, nodes=nodes)
        expanded: list[dict[str, Any]] = []
        for node_name in resolved_nodes:
            item = deepcopy(normalized)
            item["target"] = node_name
            item["targetRef"] = {"kind": "vm", "name": node_name}
            expanded.append(item)
        return expanded

    raw_targets = normalized.get("targets")
    if not isinstance(raw_targets, list):
        return [normalized]

    resolved_targets: list[str] = []
    for index, raw_target in enumerate(raw_targets):
        if isinstance(raw_target, dict):
            ref = {
                "kind": str(raw_target.get("kind") or "").strip().lower(),
                "name": str(raw_target.get("name") or "").strip(),
            }
            if not ref["kind"] or not ref["name"]:
                raise TargetResolutionError(
                    f"Action field 'targets[{index}]' object requires non-empty 'kind' and 'name'."
                )
        else:
            ref = _target_ref_from_string(str(raw_target or ""))
        if ref["kind"] == "cluster":
            raise TargetResolutionError("Target kind 'cluster' is reserved and not implemented yet.")
        if ref["kind"] not in {"vm", "site"}:
            raise TargetResolutionError(
                f"Unsupported target kind '{ref['kind']}' in action field 'targets[{index}]'."
            )
        resolved_targets.extend(resolve_target_ref_to_vm_nodes(target_ref=ref, nodes=nodes))

    deduped_targets = sorted({item for item in resolved_targets if item})
    normalized["targets"] = deduped_targets
    return [normalized]

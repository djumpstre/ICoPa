from __future__ import annotations

import pytest

from icopa_core.task_executor.action_center import (
    ActionCenterError,
    action_catalog_entries,
    normalize_action_payload,
    supported_action_types,
)


def _nodes() -> list[dict]:
    return [
        {"name": "vm_home", "kind": "vm", "labels": {"site": "local"}},
        {"name": "cloud_vm_bw", "kind": "vm", "labels": {"site": "cloud"}},
    ]


def test_action_catalog_loads() -> None:
    entries = action_catalog_entries()
    assert entries
    assert all(item.get("scope") == "vm" for item in entries)
    assert "run_runtime_preset" in supported_action_types()


def test_normalize_action_payload_rejects_legacy_exec_action() -> None:
    with pytest.raises(ActionCenterError, match="execAction is not supported"):
        normalize_action_payload(
            raw_action={"execAction": "wait", "parameters": {"seconds": 1}},
            nodes=_nodes(),
            field_path="phaseTemplates[p].actions[0]",
        )


def test_normalize_action_payload_resolves_target_ref() -> None:
    actions = normalize_action_payload(
        raw_action={
            "type": "check_connectivity",
            "targetRef": "vm:vm_home",
            "parameters": {},
        },
        nodes=_nodes(),
        field_path="phaseTemplates[p].actions[0]",
    )
    assert len(actions) == 1
    assert actions[0]["type"] == "check_connectivity"
    assert actions[0]["target"] == "vm_home"
    assert actions[0]["targetRef"] == {"kind": "vm", "name": "vm_home"}


def test_normalize_action_payload_resolves_collect_metrics_target_ref() -> None:
    actions = normalize_action_payload(
        raw_action={
            "type": "collect_metrics",
            "targetRef": "vm:vm_home",
            "parameters": {},
        },
        nodes=_nodes(),
        field_path="phaseTemplates[p].actions[0]",
    )
    assert len(actions) == 1
    assert actions[0]["type"] == "collect_metrics"
    assert actions[0]["target"] == "vm_home"

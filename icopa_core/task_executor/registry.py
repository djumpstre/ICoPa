"""Action executor registry for profiling action payloads."""

from __future__ import annotations

from typing import Any

from .base_exec import ActionExecutionError, ExecutionContext, ProfilingBaseExec
from .wait_exec import WaitExec
from .vm_exec import VMConnectCheckExec, VMMetricCollectionExec, VMProfilingBaseExec, VMProfilingProbeExec
from .vm_general_actions.graph_check import GraphPingCheckExec
from .vm_general_actions.vm_cleanup import VMCleanupContainersExec
from .vm_general_actions.vm_cleanup_zenoh_routers import VMCleanupZenohRoutersExec
from .vm_general_actions.vm_stress_setup import VMStressContainerSetupExec
from .vm_runtime_actions.vm_zenoh_profiling import VMZenohProfilingProbeExec
from .vm_runtime_actions.vm_zenoh_routing_setup import VMProfilingRoutingSetupExec, VMZenohRoutingSetupExec

_BASE_REGISTRY: dict[str, type[ProfilingBaseExec]] = {
    "wait": WaitExec,
    "check_connectivity": VMConnectCheckExec,
    "check_graph_ping": GraphPingCheckExec,
    "run_runtime_preset": VMProfilingBaseExec,
    "cleanup_containers": VMCleanupContainersExec,
    "cleanup_zenoh_routers": VMCleanupZenohRoutersExec,
    "collect_metrics": VMMetricCollectionExec,
}


def _runtime_zenoh_tags(runtime_env: dict[str, Any]) -> dict[str, Any]:
    tags = runtime_env.get("tags")
    zenoh = tags.get("zenoh") if isinstance(tags, dict) else None
    return zenoh if isinstance(zenoh, dict) else {}


def resolve_executor_class(action: dict[str, Any]) -> type[ProfilingBaseExec]:
    """Resolve executor class from one action payload."""
    action_type = str(action.get("type") or "")
    if not action_type:
        raise ActionExecutionError("Action payload requires non-empty field 'type'.")

    if action_type == "run_runtime_preset":
        preset = str(action.get("preset") or "")
        group = str(action.get("group") or "")
        if preset.startswith("stress/") or group == "stress":
            return VMStressContainerSetupExec
        if preset.startswith("routing/") or group == "routing":
            return VMProfilingRoutingSetupExec
        if preset.startswith("profiling/") or group in {"profiling", "testing"}:
            return VMProfilingProbeExec

    executor_class = _BASE_REGISTRY.get(action_type)
    if executor_class is None:
        raise ActionExecutionError(f"Unsupported action type '{action_type}'.")
    return executor_class


def build_executor(ctx: ExecutionContext) -> ProfilingBaseExec:
    """Build one executor instance from ``ctx.action``."""
    action_type = str(ctx.action.get("type") or "")
    if action_type == "run_runtime_preset":
        preset = str(ctx.action.get("preset") or "")
        group = str(ctx.action.get("group") or "")
        is_stress = preset.startswith("stress/") or group == "stress"
        is_routing = preset.startswith("routing/") or group == "routing"
        is_profiling = preset.startswith("profiling/") or group in {"profiling", "testing"}
        if group == "stress" and preset and "/" not in preset:
            ctx.action["preset"] = f"stress/{preset}"
            preset = str(ctx.action["preset"])
        if group == "routing" and preset and "/" not in preset:
            ctx.action["preset"] = f"routing/{preset}"
            preset = str(ctx.action["preset"])
        if group in {"profiling", "testing"} and preset and "/" not in preset:
            ctx.action["preset"] = f"profiling/{preset}"
            preset = str(ctx.action["preset"])
        if is_stress:
            return VMStressContainerSetupExec(ctx)
        if is_routing:
            zenoh = _runtime_zenoh_tags(ctx.runtime_env)
            if isinstance(zenoh, dict) and (bool(zenoh) or zenoh.get("enabled") is True):
                return VMZenohRoutingSetupExec(ctx)
        if is_profiling:
            zenoh = _runtime_zenoh_tags(ctx.runtime_env)
            if isinstance(zenoh, dict) and (bool(zenoh) or zenoh.get("enabled") is True):
                return VMZenohProfilingProbeExec(ctx)
    return resolve_executor_class(ctx.action)(ctx)

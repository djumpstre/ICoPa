"""Execution action templates for profiling workflows.

This package is a new, typed action layer.
Legacy modules currently live under ``task_executor/action_exec``.
"""

from .base_exec import ActionExecutionError, ActionExecutionResult, ExecutionContext, ProfilingBaseExec
from .action_center import ActionCenterError, action_catalog_entries, normalize_action_payload, supported_action_types
from .registry import build_executor, resolve_executor_class
from .wait_exec import WaitExec
from .vm_exec import VMConnectCheckExec, VMMetricCollectionExec, VMProfilingBaseExec, VMProfilingProbeExec
from .vm_general_actions.graph_check import GraphPingCheckExec
from .vm_general_actions.vm_cleanup import VMCleanupContainersExec
from .vm_general_actions.vm_cleanup_zenoh_routers import VMCleanupZenohRoutersExec
from .vm_general_actions.vm_stress_setup import VMStressContainerSetupExec
from .vm_runtime_actions.vm_zenoh_profiling import VMZenohProfilingProbeExec
from .vm_runtime_actions.vm_zenoh_routing_setup import VMProfilingRoutingSetupExec, VMZenohRoutingSetupExec

__all__ = [
    "ActionExecutionError",
    "ActionExecutionResult",
    "ActionCenterError",
    "ExecutionContext",
    "GraphPingCheckExec",
    "WaitExec",
    "ProfilingBaseExec",
    "VMConnectCheckExec",
    "VMCleanupContainersExec",
    "VMCleanupZenohRoutersExec",
    "VMZenohProfilingProbeExec",
    "VMMetricCollectionExec",
    "VMProfilingBaseExec",
    "VMProfilingProbeExec",
    "VMStressContainerSetupExec",
    "VMProfilingRoutingSetupExec",
    "VMZenohRoutingSetupExec",
    "action_catalog_entries",
    "resolve_executor_class",
    "normalize_action_payload",
    "supported_action_types",
    "build_executor",
]

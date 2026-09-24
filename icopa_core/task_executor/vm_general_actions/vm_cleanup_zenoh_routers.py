"""Cleanup executor for Zenoh router containers on VM targets."""

from __future__ import annotations

from ..base_exec import ActionExecutionResult
from .vm_cleanup import VMCleanupContainersExec, normalize_container_names

DEFAULT_ZENOH_ROUTER_CONTAINER_NAMES = ["icopa-zenoh-router", "zenoh-router"]

class VMCleanupZenohRoutersExec(VMCleanupContainersExec):
    """Stop and remove known Zenoh router containers on one VM."""

    action_type = "cleanup_zenoh_routers"

    def validate_input(self) -> None:
        if not normalize_container_names(self.ctx.action):
            self.ctx.action["container_names"] = list(DEFAULT_ZENOH_ROUTER_CONTAINER_NAMES)
        super().validate_input()

    def execute_impl(self) -> ActionExecutionResult:
        if not normalize_container_names(self.ctx.action):
            self.ctx.action["container_names"] = list(DEFAULT_ZENOH_ROUTER_CONTAINER_NAMES)
        result = super().execute_impl()
        result.action_type = self.action_type
        result.message = (
            f"Zenoh router cleanup completed on '{result.target}'."
            if result.success
            else f"Zenoh router cleanup failed on '{result.target}'."
        )
        return result

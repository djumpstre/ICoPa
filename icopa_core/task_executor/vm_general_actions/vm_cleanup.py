"""Generic cleanup executor for VM containers with deletion polling."""

from __future__ import annotations

import time
from typing import Any

from ..base_exec import ActionExecutionError, ActionExecutionResult
from ..vm_exec import VMExecBase

DEFAULT_CONTAINER_DELETE_POLL_INTERVAL_SEC = 2
DEFAULT_CONTAINER_DELETE_TIMEOUT_SEC = 20


def normalize_container_names(
    action: dict[str, Any],
    *,
    default_names: list[str] | None = None,
) -> list[str]:
    names: list[str] = []
    raw_list = action.get("container_names")
    if not raw_list and isinstance(action.get("parameters"), dict):
        raw_list = (action.get("parameters") or {}).get("container_names")
    if isinstance(raw_list, list):
        for item in raw_list:
            text = str(item or "").strip()
            if text and text not in names:
                names.append(text)
    elif isinstance(raw_list, str):
        text = raw_list.strip()
        if text:
            names.append(text)

    raw_single = action.get("container_name")
    if not raw_single and isinstance(action.get("parameters"), dict):
        raw_single = (action.get("parameters") or {}).get("container_name")
    if raw_single:
        text = str(raw_single).strip()
        if text and text not in names:
            names.append(text)

    if names:
        return names

    defaults = default_names or []
    return [str(item).strip() for item in defaults if str(item).strip()]


def _as_positive_int(value: Any, *, field_name: str, default: int) -> int:
    raw = default if value in (None, "") else value
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError(f"{field_name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ActionExecutionError(f"{field_name} must be a positive integer.")
    return parsed


class VMCleanupContainersExec(VMExecBase):
    """Stop/remove one or many containers and verify they are deleted."""

    action_type = "cleanup_containers"

    def validate_input(self) -> None:
        target = str(self.ctx.require_action_field("target"))
        if target not in self.ctx.inventory_by_node:
            raise ActionExecutionError(f"Target VM '{target}' not found in inventory_by_node.")
        names = normalize_container_names(self.ctx.action)
        if not names:
            raise ActionExecutionError("cleanup_containers requires non-empty container_names/container_name.")

    def execute_impl(self) -> ActionExecutionResult:
        target = str(self.ctx.require_action_field("target"))
        vm = self.ctx.inventory_by_node[target]
        action_params = self.ctx.action.get("parameters") if isinstance(self.ctx.action.get("parameters"), dict) else {}
        timeout_sec = _as_positive_int(
            action_params.get("timeout_sec", self.ctx.action.get("timeout_sec")),
            field_name="timeout_sec",
            default=30,
        )
        delete_timeout_sec = _as_positive_int(
            action_params.get("delete_timeout_sec", self.ctx.action.get("delete_timeout_sec")),
            field_name="delete_timeout_sec",
            default=DEFAULT_CONTAINER_DELETE_TIMEOUT_SEC,
        )
        poll_interval_sec = _as_positive_int(
            action_params.get("poll_interval_sec", self.ctx.action.get("poll_interval_sec")),
            field_name="poll_interval_sec",
            default=DEFAULT_CONTAINER_DELETE_POLL_INTERVAL_SEC,
        )
        container_names = normalize_container_names(self.ctx.action)
        ssh_client = self._build_ssh_client(vm)

        per_container: list[dict[str, Any]] = []
        all_ok = True
        for container_name in container_names:
            force_remove_result = ssh_client.execute_command(
                f"docker rm -f {container_name} >/dev/null 2>&1 || true",
                timeout=timeout_sec,
            )
            deleted = False
            polls = 0
            last_status = ""
            deadline = time.time() + delete_timeout_sec
            while time.time() <= deadline:
                polls += 1
                status = ssh_client.get_container_status(container_name, timeout=min(20, timeout_sec))
                last_status = status.status
                if not status.exists:
                    deleted = True
                    break
                time.sleep(poll_interval_sec)

            item = {
                "container_name": container_name,
                "delete_verified": deleted,
                "polls": polls,
                "last_status": last_status,
                "force_remove_returncode": force_remove_result.returncode,
                "force_remove_stderr": force_remove_result.stderr,
            }
            per_container.append(item)
            if not deleted:
                all_ok = False

        return ActionExecutionResult(
            action_type=self.action_type,
            target=target,
            success=all_ok,
            status="SUCCESS" if all_ok else "FAILED",
            message=(
                f"Container cleanup completed on '{target}'."
                if all_ok
                else f"Container cleanup failed on '{target}'."
            ),
            debug={
                "target": target,
                "container_names": container_names,
                "timeout_sec": timeout_sec,
                "delete_timeout_sec": delete_timeout_sec,
                "poll_interval_sec": poll_interval_sec,
                "containers": per_container,
            },
        )

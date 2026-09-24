"""Base execution contracts for experiment actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class ActionExecutionError(Exception):
    """Raised when an action cannot be validated or executed."""


@dataclass(slots=True)
class ActionExecutionResult:
    """Normalized action execution result."""

    action_type: str
    target: str | None
    success: bool
    status: str
    message: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    artifacts: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionContext:
    """Normalized inputs from hub resources for one action execution.

    The caller (hub/service layer) is expected to resolve and inject:
    - inventory_by_node: nodeName -> inventory vm dict
    - runtime_env: runtime env profile dict
    - scenario: scenario dict
    - experiment: experiment plan dict
    - phase: current phase dict
    - action: current action dict
    """

    inventory_by_node: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_env: dict[str, Any] = field(default_factory=dict)
    scenario: dict[str, Any] = field(default_factory=dict)
    experiment: dict[str, Any] = field(default_factory=dict)
    phase: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, Any] = field(default_factory=dict)
    workdir: str = ""

    def require_action_field(self, name: str) -> Any:
        value = self.action.get(name)
        if value in (None, ""):
            raise ActionExecutionError(f"Action requires field '{name}'.")
        return value

    def get_target_vm(self) -> dict[str, Any]:
        target = self.require_action_field("target")
        vm = self.inventory_by_node.get(str(target))
        if not vm:
            raise ActionExecutionError(f"Target VM '{target}' not found in inventory_by_node.")
        return vm

    def get_runtime_presets(self) -> list[dict[str, Any]]:
        presets = self.runtime_env.get("command_preset") or self.runtime_env.get("commandPresets") or []
        if not isinstance(presets, list):
            raise ActionExecutionError("Runtime env command presets must be a list.")
        return [item for item in presets if isinstance(item, dict)]


class ProfilingBaseExec:
    """Transport-neutral action base class.

    Lifecycle:
    1) validate_input
    2) prepare
    3) execute_impl
    4) finalize
    """

    action_type = "base"

    def __init__(self, ctx: ExecutionContext):
        self.ctx = ctx

    def validate_input(self) -> None:
        """Validate required context/action fields."""

    def prepare(self) -> None:
        """Optional setup before execution."""

    def execute_impl(self) -> ActionExecutionResult:
        raise NotImplementedError

    def finalize(self, result: ActionExecutionResult) -> ActionExecutionResult:
        """Optional result normalization."""
        return result

    def execute(self) -> ActionExecutionResult:
        started_at = datetime.now(timezone.utc)
        self.validate_input()
        self.prepare()
        result = self.execute_impl()
        result = self.finalize(result)
        result.started_at = started_at
        result.finished_at = datetime.now(timezone.utc)
        return result

"""Graph connectivity execution templates."""

from __future__ import annotations

import re
import statistics
from typing import Any

from ..base_exec import ActionExecutionError, ActionExecutionResult
from ..vm_exec import VMExecBase

DEFAULT_GRAPH_PING_DURATION_SEC = 60
DEFAULT_GRAPH_PING_STEP_SEC = 1.0
DEFAULT_GRAPH_PING_WAIT_SEC = 2
DEFAULT_GRAPH_PING_TIMEOUT_SEC = 90

_PING_TIME_PATTERN = re.compile(r"time=([0-9]+(?:\.[0-9]+)?)\s*ms")
_PING_SUMMARY_PATTERN = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*"
    r"([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)\s*ms"
)


def _resolve_int(raw_value: Any, *, field_name: str, default: int, minimum: int) -> int:
    if raw_value in (None, ""):
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError(f"Field '{field_name}' must be an integer.") from exc
    if value < minimum:
        raise ActionExecutionError(f"Field '{field_name}' must be >= {minimum}.")
    return value


def _resolve_float(raw_value: Any, *, field_name: str, default: float, minimum: float) -> float:
    if raw_value in (None, ""):
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError(f"Field '{field_name}' must be a number.") from exc
    if value < minimum:
        raise ActionExecutionError(f"Field '{field_name}' must be >= {minimum}.")
    return value


def _extract_ping_latency_metrics(stdout: str) -> dict[str, Any]:
    summary_match = _PING_SUMMARY_PATTERN.search(stdout or "")
    if summary_match:
        latency_min_ms = float(summary_match.group(1))
        latency_avg_ms = float(summary_match.group(2))
        latency_max_ms = float(summary_match.group(3))
        latency_variance_ms = float(summary_match.group(4))
        return {
            "latency_min_ms": latency_min_ms,
            "latency_avg_ms": latency_avg_ms,
            "latency_max_ms": latency_max_ms,
            "latency_variance_ms": latency_variance_ms,
        }

    samples = [float(match.group(1)) for match in _PING_TIME_PATTERN.finditer(stdout or "")]
    if not samples:
        return {}
    return {
        "latency_min_ms": min(samples),
        "latency_avg_ms": statistics.mean(samples),
        "latency_max_ms": max(samples),
        "latency_variance_ms": statistics.pvariance(samples) if len(samples) > 1 else 0.0,
        "latency_samples": len(samples),
    }


class GraphPingCheckExec(VMExecBase):
    """Execute ICMP ping from one VM node to another VM node in scenario graph."""

    action_type = "check_graph_ping"
    _default_source_node = "vm_home"
    _default_target_node = "cloud_vm_bw"

    def _parameters(self) -> dict[str, Any]:
        params = self.ctx.action.get("parameters")
        return params if isinstance(params, dict) else {}

    def _resolve_source_node(self) -> str:
        params = self._parameters()
        return str(
            self.ctx.action.get("target")
            or params.get("source")
            or params.get("source_node")
            or self.ctx.action.get("source")
            or self.ctx.action.get("source_node")
            or self._default_source_node
        )

    def _resolve_target_node(self) -> str:
        params = self._parameters()
        return str(
            params.get("target")
            or params.get("target_node")
            or self.ctx.action.get("target_node")
            or self.ctx.action.get("destination_node")
            or self._default_target_node
        )

    def _resolve_destination_address(self, target_vm: dict[str, Any]) -> str:
        params = self._parameters()
        address = str(
            params.get("destination_address")
            or params.get("destination")
            or self.ctx.action.get("destination_address")
            or self.ctx.action.get("destination")
            or target_vm.get("address")
            or ""
        )
        if not address:
            raise ActionExecutionError("Target VM is missing required field 'address'.")
        return address

    def validate_input(self) -> None:
        source_node = self._resolve_source_node()
        target_node = self._resolve_target_node()
        if source_node not in self.ctx.inventory_by_node:
            raise ActionExecutionError(f"Source VM node '{source_node}' not found in inventory_by_node.")
        if target_node not in self.ctx.inventory_by_node:
            raise ActionExecutionError(f"Target VM node '{target_node}' not found in inventory_by_node.")
        target_vm = self.ctx.inventory_by_node[target_node]
        self._resolve_destination_address(target_vm)
        params = self._parameters()
        _resolve_int(
            params.get("duration_sec", self.ctx.action.get("duration_sec")),
            field_name="duration_sec",
            default=DEFAULT_GRAPH_PING_DURATION_SEC,
            minimum=1,
        )
        _resolve_float(
            params.get("step_sec", self.ctx.action.get("step_sec")),
            field_name="step_sec",
            default=DEFAULT_GRAPH_PING_STEP_SEC,
            minimum=0.1,
        )
        _resolve_int(
            params.get("wait_sec", self.ctx.action.get("wait_sec")),
            field_name="wait_sec",
            default=DEFAULT_GRAPH_PING_WAIT_SEC,
            minimum=1,
        )
        _resolve_int(
            params.get("timeout_sec", self.ctx.action.get("timeout_sec")),
            field_name="timeout_sec",
            default=DEFAULT_GRAPH_PING_TIMEOUT_SEC,
            minimum=1,
        )

    def execute_impl(self) -> ActionExecutionResult:
        source_node = self._resolve_source_node()
        target_node = self._resolve_target_node()
        source_vm = self.ctx.inventory_by_node[source_node]
        target_vm = self.ctx.inventory_by_node[target_node]
        destination = self._resolve_destination_address(target_vm)
        params = self._parameters()

        duration_sec = _resolve_int(
            params.get("duration_sec", self.ctx.action.get("duration_sec")),
            field_name="duration_sec",
            default=DEFAULT_GRAPH_PING_DURATION_SEC,
            minimum=1,
        )
        step_sec = _resolve_float(
            params.get("step_sec", self.ctx.action.get("step_sec")),
            field_name="step_sec",
            default=DEFAULT_GRAPH_PING_STEP_SEC,
            minimum=0.1,
        )
        count = max(1, int(duration_sec / step_sec))
        wait_sec = _resolve_int(
            params.get("wait_sec", self.ctx.action.get("wait_sec")),
            field_name="wait_sec",
            default=DEFAULT_GRAPH_PING_WAIT_SEC,
            minimum=1,
        )
        timeout_sec = _resolve_int(
            params.get("timeout_sec", self.ctx.action.get("timeout_sec")),
            field_name="timeout_sec",
            default=DEFAULT_GRAPH_PING_TIMEOUT_SEC,
            minimum=1,
        )
        command = f"ping -c {count} -i {step_sec:g} -W {wait_sec} {destination}"

        ssh_client = self._build_ssh_client(source_vm)
        command_result = ssh_client.execute_command(command, timeout=timeout_sec)
        success = command_result.success
        latency_metrics = _extract_ping_latency_metrics(command_result.stdout)
        metrics = {
            "duration_sec": duration_sec,
            "step_sec": step_sec,
            "count": count,
            **latency_metrics,
        }
        return ActionExecutionResult(
            action_type=self.action_type,
            target=source_node,
            success=success,
            status="SUCCESS" if success else "FAILED",
            message=(
                f"Ping from '{source_node}' to '{target_node}' succeeded."
                if success
                else f"Ping from '{source_node}' to '{target_node}' failed."
            ),
            logs=[command_result.stdout] if command_result.stdout else [],
            metrics=metrics,
            debug={
                "source_node": source_node,
                "target_node": target_node,
                "source_vm": {
                    "host": source_vm.get("address", ""),
                    "user": source_vm.get("user_name", ""),
                    "port": source_vm.get("port", 22),
                },
                "target_vm": {
                    "host": target_vm.get("address", ""),
                    "user": target_vm.get("user_name", ""),
                    "port": target_vm.get("port", 22),
                },
                "destination_address": destination,
                "command": command,
                "returncode": command_result.returncode,
                "stderr": command_result.stderr,
                "metrics": metrics,
            },
        )

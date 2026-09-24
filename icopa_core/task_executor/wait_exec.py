"""Executor for wait actions."""

from __future__ import annotations

import time

from .base_exec import ActionExecutionError, ActionExecutionResult, ProfilingBaseExec


class WaitExec(ProfilingBaseExec):
    """Pause the pipeline for a requested duration."""

    action_type = "wait"

    def _seconds(self) -> float:
        params = self.ctx.action.get("parameters")
        if not isinstance(params, dict):
            raise ActionExecutionError("wait action requires object field 'parameters'.")
        raw_seconds = params.get("seconds")
        if raw_seconds in (None, ""):
            raise ActionExecutionError("wait action requires field 'parameters.seconds'.")
        try:
            seconds = float(raw_seconds)
        except (TypeError, ValueError) as exc:
            raise ActionExecutionError("wait action field 'parameters.seconds' must be numeric.") from exc
        if seconds < 0:
            raise ActionExecutionError("wait action field 'parameters.seconds' must be >= 0.")
        return seconds

    def validate_input(self) -> None:
        self._seconds()

    def execute_impl(self) -> ActionExecutionResult:
        seconds = self._seconds()
        time.sleep(seconds)
        return ActionExecutionResult(
            action_type=self.action_type,
            target=None,
            success=True,
            status="SUCCESS",
            message=f"Waited {seconds:g}s.",
            debug={"seconds": seconds},
        )

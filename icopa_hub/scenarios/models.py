"""Models for scenario profiles."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class Scenario(models.Model):
    """Scenario imported from YAML."""

    class CheckStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        SKIPPED = "SKIPPED", "Skipped"

    class ValidationStatus(models.TextChoices):
        IDLE = "IDLE", "Idle"
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=64, default="Scenario")
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    nodes = models.JSONField(default=list, blank=True)
    graph = models.JSONField(default=dict, blank=True)
    runtime_env = models.JSONField(default=list, blank=True)
    payloads = models.JSONField(default=list, blank=True)
    background_workloads = models.JSONField(default=list, blank=True)
    check_status_node = models.CharField(max_length=16, choices=CheckStatus.choices, default=CheckStatus.UNKNOWN)
    check_status_graph = models.CharField(max_length=16, choices=CheckStatus.choices, default=CheckStatus.UNKNOWN)
    check_status_actions = models.CharField(max_length=16, choices=CheckStatus.choices, default=CheckStatus.UNKNOWN)
    validation_requested = models.BooleanField(default=False)
    validation_status = models.CharField(
        max_length=16,
        choices=ValidationStatus.choices,
        default=ValidationStatus.IDLE,
    )
    last_validation_task_id = models.CharField(max_length=120, blank=True)
    last_validation_at = models.DateTimeField(null=True, blank=True)
    last_validation_data = models.JSONField(default=dict, blank=True)
    validation_trace = models.JSONField(default=list, blank=True)
    validation_history = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    raw_yaml = models.TextField(blank=True, default="")
    cached_yaml_file = models.FileField(upload_to="static/scenarios_cache/", blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scenarios",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_scenarios",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("created_by", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"

    def compute_validation_status(self) -> str:
        """Aggregate validation status from actions/node/graph check statuses."""
        check_statuses = [
            self.check_status_actions,
            self.check_status_node,
            self.check_status_graph,
        ]
        if any(status == self.CheckStatus.RUNNING for status in check_statuses):
            return self.ValidationStatus.RUNNING
        if any(status == self.CheckStatus.PENDING for status in check_statuses):
            return self.ValidationStatus.PENDING
        if any(status == self.CheckStatus.FAIL for status in check_statuses):
            return self.ValidationStatus.FAILED
        if all(status == self.CheckStatus.PASS for status in check_statuses):
            return self.ValidationStatus.SUCCEEDED
        return self.ValidationStatus.IDLE

    def graph_edges(self) -> list[dict[str, Any]]:
        graph_payload = self.graph if isinstance(self.graph, dict) else {}
        edges = graph_payload.get("edges")
        if not isinstance(edges, list):
            return []
        normalized: list[dict[str, Any]] = []
        for idx, edge in enumerate(edges, start=1):
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or "").strip()
            target = str(edge.get("to") or "").strip()
            if not source or not target:
                continue
            edge_name = str(edge.get("name") or f"edge-{idx}").strip()
            normalized.append(
                {
                    "name": edge_name,
                    "from": source,
                    "to": target,
                    "type": str(edge.get("type") or "").strip(),
                    "link": edge.get("link"),
                }
            )
        return normalized

    def graph_edge_names(self) -> list[str]:
        return [str(edge.get("name") or "") for edge in self.graph_edges()]

    def graph_edge_count(self) -> int:
        return len(self.graph_edges())

    def vm_node_count(self) -> int:
        nodes_payload = self.nodes if isinstance(self.nodes, list) else []
        count = 0
        for node in nodes_payload:
            if not isinstance(node, dict):
                continue
            node_name = str(node.get("nodeName") or node.get("name") or "").strip()
            node_kind = str(node.get("kind") or node.get("type") or "").strip().lower()
            if node_name and node_kind == "vm":
                count += 1
        return count

    def get_node_check_summary(self, validation_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = validation_payload if isinstance(validation_payload, dict) else {}
        checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
        nodes_check = checks.get("nodes") if isinstance(checks, dict) and isinstance(checks.get("nodes"), dict) else {}
        check_requested = bool(payload.get("check_nodes", bool(nodes_check)))
        raw_results = nodes_check.get("results") if isinstance(nodes_check.get("results"), list) else []

        node_results: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            node_name = str(item.get("node") or item.get("target") or "").strip() or f"node-{idx}"
            ok_value = item.get("ok")
            ok_flag = bool(ok_value) if ok_value is not None else None
            node_results.append(
                {
                    "node": node_name,
                    "ok": ok_flag,
                    "status": str(
                        item.get("status")
                        or ("SUCCESS" if ok_flag else "FAILED" if ok_flag is False else "UNKNOWN")
                    ),
                    "message": str(item.get("message") or item.get("error") or ""),
                    "managed_containers": (
                        item.get("managed_containers") if isinstance(item.get("managed_containers"), list) else []
                    ),
                    "managed_container_status": (
                        item.get("managed_container_status")
                        if isinstance(item.get("managed_container_status"), list)
                        else []
                    ),
                }
            )

        passed_nodes = sum(1 for item in node_results if item.get("ok") is True)
        failed_nodes = sum(1 for item in node_results if item.get("ok") is False)
        checked_nodes = nodes_check.get("checked_nodes")
        if not isinstance(checked_nodes, int):
            checked_nodes = len(node_results)
        checked_nodes = max(checked_nodes, len(node_results))
        skipped_flag = bool(nodes_check.get("skipped"))

        if skipped_flag or not check_requested:
            status = "SKIPPED"
        elif failed_nodes > 0 or (nodes_check and nodes_check.get("ok") is False):
            status = "FAIL"
        elif nodes_check and nodes_check.get("ok") is True:
            status = "PASS"
        else:
            status = "UNKNOWN"

        return {
            "status": status,
            "requested": check_requested,
            "total_nodes": self.vm_node_count(),
            "checked_nodes": checked_nodes,
            "passed_nodes": passed_nodes,
            "failed_nodes": failed_nodes,
            "results": node_results,
            "checked_at": "",
        }

    def get_graph_check_summary(self, validation_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = validation_payload if isinstance(validation_payload, dict) else {}
        checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
        graph_check = checks.get("graph") if isinstance(checks, dict) and isinstance(checks.get("graph"), dict) else {}
        check_requested = bool(payload.get("check_graph", bool(graph_check)))
        graph_edges = self.graph_edges()
        raw_results = graph_check.get("results") if isinstance(graph_check.get("results"), list) else []

        edge_results: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            edge_name = str(item.get("edge_name") or "").strip()
            source = str(item.get("source_node") or item.get("source") or "").strip()
            target = str(item.get("target_node") or item.get("destination") or "").strip()
            if not edge_name:
                if source and target:
                    edge_name = f"{source}->{target}"
                else:
                    edge_name = f"edge-{idx}"
            ok_value = item.get("ok")
            ok_flag = bool(ok_value) if ok_value is not None else None
            edge_results.append(
                {
                    "edge_name": edge_name,
                    "source_node": source,
                    "target_node": target,
                    "ok": ok_flag,
                    "status": str(item.get("status") or ("SUCCESS" if ok_flag else "FAILED" if ok_flag is False else "UNKNOWN")),
                    "message": str(item.get("message") or item.get("error") or ""),
                    "metrics": item.get("metrics") if isinstance(item.get("metrics"), dict) else {},
                }
            )

        passed_edges = sum(1 for item in edge_results if item.get("ok") is True)
        failed_edges = sum(1 for item in edge_results if item.get("ok") is False)
        checked_edges = graph_check.get("checked_edges")
        if not isinstance(checked_edges, int):
            checked_edges = len(edge_results)
        checked_edges = max(checked_edges, len(edge_results))
        skipped_flag = bool(graph_check.get("skipped"))

        if skipped_flag or not check_requested:
            status = "SKIPPED"
        elif failed_edges > 0 or (graph_check and graph_check.get("ok") is False):
            status = "FAIL"
        elif graph_check and graph_check.get("ok") is True:
            status = "PASS"
        else:
            status = "UNKNOWN"

        return {
            "status": status,
            "requested": check_requested,
            "total_edges": len(graph_edges),
            "checked_edges": checked_edges,
            "passed_edges": passed_edges,
            "failed_edges": failed_edges,
            "results": edge_results,
        }

    @staticmethod
    def _is_failed_action_result(item: dict[str, Any]) -> bool:
        success = item.get("success")
        if success is False:
            return True
        status = str(item.get("status") or "").strip().upper()
        return status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}

    @staticmethod
    def _first_line(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text.splitlines()[0].strip()

    @staticmethod
    def _append_failure_entry(entries: list[dict[str, Any]], *, scope: str, code: str, message: str, **meta: Any) -> None:
        entry = {
            "scope": scope,
            "code": code,
            "message": str(message or "").strip(),
        }
        for key, value in meta.items():
            if value in (None, "", [], {}):
                continue
            entry[key] = value
        entries.append(entry)

    def build_failure_report(self, validation_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = validation_payload if isinstance(validation_payload, dict) else {}
        checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
        entries: list[dict[str, Any]] = []

        top_error = str(payload.get("error") or "").strip()
        if top_error:
            self._append_failure_entry(
                entries,
                scope="orchestrator",
                code="validation_failed",
                message=top_error,
            )

        actions = checks.get("actions") if isinstance(checks.get("actions"), dict) else {}
        if actions and not bool(actions.get("ok")) and not bool(actions.get("skipped")):
            execution = actions.get("execution") if isinstance(actions.get("execution"), dict) else {}
            phase_name = str(
                execution.get("phase_name")
                or payload.get("phase_name")
                or ""
            ).strip()
            action_results = execution.get("results") if isinstance(execution.get("results"), list) else []
            for item in action_results:
                if not isinstance(item, dict):
                    continue
                if not self._is_failed_action_result(item):
                    continue
                debug = item.get("debug") if isinstance(item.get("debug"), dict) else {}
                stderr_line = self._first_line(debug.get("stderr"))
                message = str(item.get("message") or "").strip()
                if not message:
                    message = stderr_line or "Action execution failed."
                elif stderr_line and stderr_line not in message:
                    message = f"{message} | {stderr_line}"
                self._append_failure_entry(
                    entries,
                    scope="actions",
                    code="action_execution_failed",
                    message=message,
                    phase_name=phase_name,
                    index=item.get("index"),
                    action_type=item.get("type"),
                    target=item.get("target"),
                    status=item.get("status"),
                    returncode=debug.get("returncode"),
                )
            for err in (actions.get("errors") or [])[:5]:
                self._append_failure_entry(
                    entries,
                    scope="actions",
                    code="action_validation_error",
                    message=str(err),
                    phase_name=phase_name,
                )
            for err in (execution.get("errors") or [])[:5]:
                self._append_failure_entry(
                    entries,
                    scope="actions",
                    code="action_execution_error",
                    message=str(err),
                    phase_name=phase_name,
                )

        nodes = checks.get("nodes") if isinstance(checks.get("nodes"), dict) else {}
        if nodes and not bool(nodes.get("ok")) and not bool(nodes.get("skipped")):
            for item in (nodes.get("results") or [])[:10]:
                if not isinstance(item, dict) or item.get("ok") is not False:
                    continue
                message = str(item.get("message") or item.get("error") or "").strip()
                stderr_line = self._first_line(item.get("stderr"))
                if not message:
                    message = stderr_line or "Node connectivity check failed."
                elif stderr_line and stderr_line not in message:
                    message = f"{message} | {stderr_line}"
                self._append_failure_entry(
                    entries,
                    scope="nodes",
                    code="node_connectivity_failed",
                    message=message,
                    node=item.get("node"),
                    status=item.get("status"),
                    returncode=item.get("returncode"),
                )
            for err in (nodes.get("errors") or [])[:5]:
                self._append_failure_entry(
                    entries,
                    scope="nodes",
                    code="node_validation_error",
                    message=str(err),
                )

        graph = checks.get("graph") if isinstance(checks.get("graph"), dict) else {}
        if graph and not bool(graph.get("ok")) and not bool(graph.get("skipped")):
            for item in (graph.get("results") or [])[:10]:
                if not isinstance(item, dict) or item.get("ok") is not False:
                    continue
                message = str(item.get("message") or item.get("error") or "").strip()
                stderr_line = self._first_line((item.get("debug") or {}).get("stderr"))
                if not message:
                    message = stderr_line or "Graph edge check failed."
                elif stderr_line and stderr_line not in message:
                    message = f"{message} | {stderr_line}"
                self._append_failure_entry(
                    entries,
                    scope="graph",
                    code="graph_edge_failed",
                    message=message,
                    edge_name=item.get("edge_name"),
                    source_node=item.get("source_node"),
                    target_node=item.get("target_node"),
                    status=item.get("status"),
                )
            graph_error = str(graph.get("error") or "").strip()
            if graph_error:
                self._append_failure_entry(
                    entries,
                    scope="graph",
                    code="graph_validation_error",
                    message=graph_error,
                )

        if not entries and payload.get("ok") is False:
            self._append_failure_entry(
                entries,
                scope="orchestrator",
                code="unknown_failure",
                message="Validation failed without a structured error entry.",
            )

        failed_scopes: list[str] = []
        for item in entries:
            scope = str(item.get("scope") or "").strip()
            if scope and scope not in failed_scopes:
                failed_scopes.append(scope)

        return {
            "has_failure": bool(entries),
            "failed_scopes": failed_scopes,
            "entry_count": len(entries),
            "summary": str((entries[0] if entries else {}).get("message") or ""),
            "entries": entries,
        }


class ScenarioValidationRunVM(models.Model):
    """Per-VM metrics snapshot linked to one scenario validation run."""

    run = models.ForeignKey(
        "ScenarioValidationRun",
        on_delete=models.CASCADE,
        related_name="vm_metrics",
    )
    vm = models.ForeignKey(
        "inventory.VM",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scenario_validation_vm_metrics",
    )
    node_name = models.CharField(max_length=120)
    phase_name = models.CharField(max_length=120, blank=True, default="")
    remote_dir = models.CharField(max_length=512, blank=True, default="")
    local_dir = models.CharField(max_length=512, blank=True, default="")
    metrics = models.JSONField(default=dict, blank=True)
    collected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("run", "node_name")
        indexes = [
            models.Index(fields=["run", "node_name"]),
            models.Index(fields=["created_at"]),
        ]

class ScenarioValidationRun(models.Model):
    """One validation execution request for one scenario."""

    class RunStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        CANCEL_REQUESTED = "CANCEL_REQUESTED", "Cancel Requested"
        CANCELED = "CANCELED", "Canceled"

    scenario = models.ForeignKey(
        Scenario,
        on_delete=models.CASCADE,
        related_name="validation_runs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scenario_validation_runs",
    )
    task_id = models.CharField(max_length=120, blank=True)
    check_actions = models.BooleanField(default=True)
    check_nodes = models.BooleanField(default=True)
    check_graph = models.BooleanField(default=True)
    phase_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    metrics_remote_dir = models.CharField(max_length=512, blank=True, default="")
    metrics_local_dir = models.CharField(max_length=512, blank=True, default="")
    need_to_collect_metrics = models.BooleanField(default=False)
    probe_metrics = models.JSONField(default=dict, blank=True)
    trace = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True, default="")
    cancel_requested = models.BooleanField(default=False)
    canceled_by_clean = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["scenario", "status"]),
            models.Index(fields=["task_id"]),
            models.Index(fields=["requested_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.scenario.name} run#{self.id} ({self.status})"

    def graph_check_summary(self) -> dict[str, Any]:
        result_payload = self.result_data if isinstance(self.result_data, dict) else {}
        return self.scenario.get_graph_check_summary(result_payload)

"""Models for profiling experiment plans."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from collections import defaultdict
from typing import Any

from django.conf import settings
from django.db import models
import yaml

_GENERATED_RESULT_BACKEND_BASE_DIR = "static/profiling_exps"
_GENERATED_RESULT_TARGET_HOST_BASE_DIR = "/tmp/icopa/generated"


class ProfilingExperiment(models.Model):
    """Experiment definition imported from plan YAML."""

    class ExperimentStatus(models.TextChoices):
        NEW = "NEW", "Not Generated"
        WAITING = "WAITING", "Waiting To Run"
        RUNNING = "RUNNING", "In Execution"
        FINISHED = "FINISHED", "Finished"
        ERROR = "ERROR", "Error"

    class RunCheckStatus(models.TextChoices):
        NOT_RUN = "NOT_RUN", "Not Run"
        PASSED = "PASSED", "Passed"
        FAILED = "FAILED", "Failed"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=64, default="ExperimentPlan")
    description = models.TextField(blank=True)
    scenario_name = models.CharField(max_length=120)
    execution = models.JSONField(default=dict, blank=True)
    selections = models.JSONField(default=dict, blank=True)
    phases = models.JSONField(default=list, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    raw_yaml = models.TextField(blank=True, default="")
    generated_payload = models.JSONField(default=dict, blank=True)
    generated_yaml = models.TextField(blank=True, default="")
    generated_source_refs = models.JSONField(default=dict, blank=True)
    generated_source_hashes = models.JSONField(default=dict, blank=True)
    total_generated_runs = models.PositiveIntegerField(default=0)
    current_gen_version = models.PositiveIntegerField(default=0)
    generated_at = models.DateTimeField(null=True, blank=True)
    run_check_status = models.CharField(
        max_length=16,
        choices=RunCheckStatus.choices,
        default=RunCheckStatus.NOT_RUN,
    )
    run_check_report = models.JSONField(default=dict, blank=True)
    run_checked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=ExperimentStatus.choices, default=ExperimentStatus.NEW)
    start_count = models.PositiveIntegerField(default=0)
    last_started_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profiling_experiments",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_profiling_experiments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("created_by", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.scenario_name})"

    def has_generated_plan(self) -> bool:
        payload = self.generated_payload if isinstance(self.generated_payload, dict) else {}
        runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
        return bool(self.total_generated_runs > 0 and runs)

    def update_run_check(self, report: dict[str, Any], *, checked_at) -> None:
        check_report = report if isinstance(report, dict) else {}
        check_ok = bool(check_report.get("ok"))
        self.run_check_status = (
            ProfilingExperiment.RunCheckStatus.PASSED
            if check_ok
            else ProfilingExperiment.RunCheckStatus.FAILED
        )
        self.run_check_report = check_report
        self.run_checked_at = checked_at

    def reset_run_check(self) -> None:
        self.run_check_status = ProfilingExperiment.RunCheckStatus.NOT_RUN
        self.run_check_report = {}
        self.run_checked_at = None

    def get_current_execution_status(self) -> dict[str, Any]:
        runs_qs = self.runs.order_by("-created_at")
        counters = {
            "total": runs_qs.count(),
            "pending": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
        }
        generation_rows: dict[int, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "pending": 0, "running": 0, "succeeded": 0, "failed": 0}
        )
        latest = runs_qs.first()
        for run in runs_qs:
            status = str(run.status)
            gen_version = int(run.gen_version or 0)
            generation_rows[gen_version]["total"] += 1
            if status == ProfilingRun.RunStatus.PENDING:
                counters["pending"] += 1
                generation_rows[gen_version]["pending"] += 1
            elif status == ProfilingRun.RunStatus.RUNNING:
                counters["running"] += 1
                generation_rows[gen_version]["running"] += 1
            elif status == ProfilingRun.RunStatus.SUCCEEDED:
                counters["succeeded"] += 1
                generation_rows[gen_version]["succeeded"] += 1
            elif status == ProfilingRun.RunStatus.FAILED:
                counters["failed"] += 1
                generation_rows[gen_version]["failed"] += 1
        generation_summary = [
            {"gen_version": gen_version, **counts}
            for gen_version, counts in sorted(generation_rows.items(), key=lambda item: item[0], reverse=True)
        ]
        return {
            **counters,
            "current_gen_version": int(self.current_gen_version or 0),
            "by_generation": generation_summary,
            "latest_run_id": latest.id if latest else None,
            "latest_run_status": latest.status if latest else "",
        }

    def derive_status_from_runs(self) -> str:
        if not self.has_generated_plan():
            return ProfilingExperiment.ExperimentStatus.NEW

        runs_qs = self.runs.all()
        if int(self.current_gen_version or 0) > 0:
            current_gen_qs = runs_qs.filter(gen_version=int(self.current_gen_version))
            if current_gen_qs.exists():
                runs_qs = current_gen_qs

        run_statuses = [str(value) for value in runs_qs.values_list("status", flat=True)]
        if not run_statuses:
            return ProfilingExperiment.ExperimentStatus.WAITING

        status_set = set(run_statuses)
        if ProfilingRun.RunStatus.RUNNING in status_set:
            return ProfilingExperiment.ExperimentStatus.RUNNING
        if ProfilingRun.RunStatus.PENDING in status_set:
            return ProfilingExperiment.ExperimentStatus.WAITING
        if status_set == {ProfilingRun.RunStatus.SUCCEEDED}:
            return ProfilingExperiment.ExperimentStatus.FINISHED
        return ProfilingExperiment.ExperimentStatus.ERROR

    def sync_status_from_runs(self, *, save: bool, modified_by=None) -> str:
        derived = self.derive_status_from_runs()
        if self.status == derived:
            return derived
        self.status = derived
        if modified_by is not None:
            self.modified_by = modified_by
        if save:
            update_fields = ["status", "updated_at"]
            if modified_by is not None:
                update_fields.append("modified_by")
            self.save(update_fields=update_fields)
        return derived

    @staticmethod
    def _edge_names_from_payload_list(raw_list: Any) -> list[str]:
        names: list[str] = []
        if not isinstance(raw_list, list):
            return names
        for item in raw_list:
            if isinstance(item, str):
                name = item.strip()
                if name and name not in names:
                    names.append(name)
        return names

    def selected_edge_names(self) -> list[str]:
        """
        Return edge names explicitly selected by the experiment YAML.

        Supported sources:
        - spec.execEdge[*].edgeRefs
        - spec.phases[*].edgeRefs / edgeRef
        """
        raw_payload = self.raw_payload if isinstance(self.raw_payload, dict) else {}
        spec = raw_payload.get("spec") if isinstance(raw_payload.get("spec"), dict) else {}
        names: list[str] = []

        exec_edge = spec.get("execEdge")
        if isinstance(exec_edge, list):
            for block in exec_edge:
                if not isinstance(block, dict):
                    continue
                for name in self._edge_names_from_payload_list(block.get("edgeRefs")):
                    if name not in names:
                        names.append(name)

        phases_sources: list[Any] = [spec.get("phases"), self.phases]
        for phase_list in phases_sources:
            if not isinstance(phase_list, list):
                continue
            for phase in phase_list:
                if not isinstance(phase, dict):
                    continue
                for name in self._edge_names_from_payload_list(phase.get("edgeRefs")):
                    if name not in names:
                        names.append(name)
                one_edge = str(phase.get("edgeRef") or "").strip()
                if one_edge and one_edge not in names:
                    names.append(one_edge)

        return names

    def get_topology_overview(self, *, user) -> dict[str, Any]:
        """
        Build simplified topology status for experiment detail page.

        - Nodes status from inventory VM model (reachability + containers runtime)
        - Edges limited to experiment-selected edgeRefs
        - Edge validation from latest scenario graph validation run when available
        """
        from inventory.models import VM
        from scenarios.models import Scenario, ScenarioValidationRun

        scenario = Scenario.objects.filter(created_by=user, name=self.scenario_name).first()
        if not scenario:
            return {"nodes": [], "edges": [], "selected_edge_names": []}

        selected_edge_names = self.selected_edge_names()
        scenario_edges = scenario.graph_edges()
        selected_edge_set = set(selected_edge_names)
        selected_edges = [edge for edge in scenario_edges if str(edge.get("name") or "") in selected_edge_set]

        fallback_nodes: list[str] = []
        for raw_node in scenario.nodes if isinstance(scenario.nodes, list) else []:
            if not isinstance(raw_node, dict):
                continue
            node_kind = str(raw_node.get("kind") or raw_node.get("type") or "").strip().lower()
            node_name = str(raw_node.get("nodeName") or raw_node.get("name") or "").strip()
            if node_kind == "vm" and node_name and node_name not in fallback_nodes:
                fallback_nodes.append(node_name)

        node_names: list[str] = []
        for edge in selected_edges:
            source = str(edge.get("from") or "").strip()
            target = str(edge.get("to") or "").strip()
            if source and source not in node_names:
                node_names.append(source)
            if target and target not in node_names:
                node_names.append(target)
        if not node_names:
            node_names = fallback_nodes

        vm_rows = VM.objects.filter(created_by=user, name__in=node_names)
        vm_by_name = {vm.name: vm for vm in vm_rows}

        nodes_payload: list[dict[str, Any]] = []
        for node_name in node_names:
            vm = vm_by_name.get(node_name)
            if not vm:
                nodes_payload.append(
                    {
                        "name": node_name,
                        "exists_in_inventory": False,
                        "reachability": "UNKNOWN",
                        "container_runtime_ready": False,
                        "managed_containers": [],
                        "managed_containers_running": [],
                    }
                )
                continue
            metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
            running = metadata.get("managed_containers_running")
            running_list = (
                [str(item).strip() for item in running if str(item).strip()]
                if isinstance(running, list)
                else [str(item).strip() for item in (vm.managed_containers or []) if str(item).strip()]
            )
            nodes_payload.append(
                {
                    "name": vm.name,
                    "exists_in_inventory": True,
                    "reachability": str(vm.status or VM.VMStatus.UNKNOWN),
                    "container_runtime_ready": bool(vm.container_runtime_ready),
                    "managed_containers": (
                        [str(item).strip() for item in (vm.managed_containers or []) if str(item).strip()]
                    ),
                    "managed_containers_running": running_list,
                }
            )

        latest_graph_run = (
            scenario.validation_runs.filter(
                check_graph=True,
                status__in=[
                    ScenarioValidationRun.RunStatus.SUCCEEDED,
                    ScenarioValidationRun.RunStatus.FAILED,
                    ScenarioValidationRun.RunStatus.CANCELED,
                ],
            )
            .order_by("-finished_at", "-requested_at")
            .first()
        )

        validation_by_name: dict[str, dict[str, Any]] = {}
        validation_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        latest_validation_meta = {
            "run_id": None,
            "checked_at": "",
            "status": "",
        }
        if latest_graph_run:
            summary = latest_graph_run.graph_check_summary()
            checked_at = latest_graph_run.finished_at.isoformat() if latest_graph_run.finished_at else ""
            latest_validation_meta = {
                "run_id": latest_graph_run.id,
                "checked_at": checked_at,
                "status": str(summary.get("status") or ""),
            }
            for item in summary.get("results") if isinstance(summary.get("results"), list) else []:
                if not isinstance(item, dict):
                    continue
                edge_name = str(item.get("edge_name") or "").strip()
                source = str(item.get("source_node") or "").strip()
                target = str(item.get("target_node") or "").strip()
                metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
                row = {
                    "ok": item.get("ok"),
                    "status": str(item.get("status") or ""),
                    "message": str(item.get("message") or ""),
                    "metrics": metrics,
                    "checked_at": checked_at,
                    "run_id": latest_graph_run.id,
                }
                if edge_name:
                    validation_by_name[edge_name] = row
                if source and target:
                    validation_by_pair[(source, target)] = row

        edges_payload: list[dict[str, Any]] = []
        for edge in selected_edges:
            edge_name = str(edge.get("name") or "").strip()
            source = str(edge.get("from") or "").strip()
            target = str(edge.get("to") or "").strip()
            validation = validation_by_name.get(edge_name) or validation_by_pair.get((source, target)) or {}
            edges_payload.append(
                {
                    "name": edge_name,
                    "from": source,
                    "to": target,
                    "type": str(edge.get("type") or ""),
                    "link": edge.get("link"),
                    "validation": validation,
                }
            )

        return {
            "nodes": nodes_payload,
            "edges": edges_payload,
            "selected_edge_names": selected_edge_names,
            "latest_graph_validation": latest_validation_meta,
        }


class ProfilingRun(models.Model):
    """Execution run record for one generated execution artifact."""

    class RunStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    experiment = models.ForeignKey(
        ProfilingExperiment,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profiling_runs",
    )
    status = models.CharField(max_length=16, choices=RunStatus.choices, default=RunStatus.PENDING)
    gen_version = models.PositiveIntegerField(default=1)
    plan_run_id = models.CharField(max_length=32, blank=True, default="")
    stop_on_failure = models.BooleanField(default=True)
    total_runs = models.PositiveIntegerField(default=0)
    completed_runs = models.PositiveIntegerField(default=0)
    failed_runs = models.PositiveIntegerField(default=0)
    run_metadata = models.JSONField(default=dict, blank=True)
    characterization_parameters = models.JSONField(default=dict, blank=True)
    compiled_payload_snapshot = models.JSONField(default=dict, blank=True)
    task_id = models.CharField(max_length=120, blank=True, default="")
    results = models.JSONField(default=list, blank=True)
    phase_action_execution_status = models.JSONField(default=dict, blank=True)
    rrt_config_execution_snapshot = models.JSONField(default=dict, blank=True)

    # metrics
    collected_metrics_path = models.CharField(max_length=512, blank=True, default="")
    metrics_collected = models.BooleanField(default=False)
    
    note = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["experiment", "status"]),
            models.Index(fields=["experiment", "gen_version", "status"]),
            models.Index(fields=["task_id"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.experiment.name} run#{self.id} ({self.status})"

    @staticmethod
    def _lookup_variant_value(
        variant_values: dict[str, Any],
        *,
        preferred_keys: tuple[str, ...],
    ) -> tuple[Any, str]:
        for key in preferred_keys:
            if key not in variant_values:
                continue
            value = variant_values.get(key)
            if value in (None, ""):
                continue
            return value, key

        for raw_key, raw_value in variant_values.items():
            key = str(raw_key or "").strip()
            if not key or raw_value in (None, ""):
                continue
            suffix = key.split(".")[-1]
            if suffix in preferred_keys:
                return raw_value, key
        return None, ""

    @classmethod
    def _iter_ros_parameter_maps(cls, payload: Any):
        if isinstance(payload, dict):
            ros_params = payload.get("ros__parameters")
            if isinstance(ros_params, dict):
                yield ros_params
            for item in payload.values():
                yield from cls._iter_ros_parameter_maps(item)
            return
        if isinstance(payload, list):
            for item in payload:
                yield from cls._iter_ros_parameter_maps(item)

    @classmethod
    def _lookup_rrt_value(
        cls,
        rrt_config_payload: Any,
        *,
        preferred_keys: tuple[str, ...],
    ) -> tuple[Any, str]:
        for ros_params in cls._iter_ros_parameter_maps(rrt_config_payload):
            for key in preferred_keys:
                if key not in ros_params:
                    continue
                value = ros_params.get(key)
                if value in (None, ""):
                    continue
                return value, key
        return None, ""

    @classmethod
    def build_characterization_parameters(
        cls,
        *,
        plan_run_id: str,
        runtime_env_name: str,
        variant_values: dict[str, Any] | None = None,
        edge: dict[str, Any] | None = None,
        rrt_config_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_variant_values = variant_values if isinstance(variant_values, dict) else {}
        normalized_edge = edge if isinstance(edge, dict) else {}
        normalized_rrt_payload = rrt_config_payload if isinstance(rrt_config_payload, dict) else {}

        payload_value, payload_key = cls._lookup_variant_value(
            normalized_variant_values,
            preferred_keys=("payload_size", "payload", "payload_bytes", "message_size", "msg_size"),
        )
        response_payload_value, response_payload_key = cls._lookup_variant_value(
            normalized_variant_values,
            preferred_keys=("response_payload_size", "response_payload", "response_payload_bytes"),
        )
        publish_rate_value, publish_rate_key = cls._lookup_variant_value(
            normalized_variant_values,
            preferred_keys=(
                "frequency_hz",
                "publish_rate_hz",
                "publish_rate",
                "publish_frequency_hz",
                "publish_frequency",
                "frequency",
                "pub_rate_hz",
            ),
        )

        if payload_value in (None, ""):
            payload_value, payload_key = cls._lookup_rrt_value(
                normalized_rrt_payload,
                preferred_keys=("payload_size", "payload_bytes", "message_size"),
            )
        if response_payload_value in (None, ""):
            response_payload_value, response_payload_key = cls._lookup_rrt_value(
                normalized_rrt_payload,
                preferred_keys=("response_payload_size", "response_payload_bytes"),
            )
        if publish_rate_value in (None, ""):
            publish_rate_value, publish_rate_key = cls._lookup_rrt_value(
                normalized_rrt_payload,
                preferred_keys=(
                    "frequency_hz",
                    "publish_rate_hz",
                    "publish_rate",
                    "publish_frequency_hz",
                    "publish_frequency",
                    "frequency",
                    "pub_rate_hz",
                ),
            )

        edge_from = str(normalized_edge.get("from") or "").strip()
        edge_to = str(normalized_edge.get("to") or "").strip()
        link = f"{edge_from} -> {edge_to}" if edge_from and edge_to else ""

        return {
            "plan_run_id": str(plan_run_id or "").strip(),
            "runtime_env_name": str(runtime_env_name or "").strip(),
            "variant_values": deepcopy(normalized_variant_values),
            "edge": deepcopy(normalized_edge),
            "payload_key": payload_key,
            "payload_value": payload_value,
            "response_payload_key": response_payload_key,
            "response_payload_value": response_payload_value,
            "publish_rate_key": publish_rate_key,
            "publish_rate_value": publish_rate_value,
            "link": link,
        }

    def get_characterization_parameters(self) -> dict[str, Any]:
        stored = self.characterization_parameters if isinstance(self.characterization_parameters, dict) else {}
        if stored:
            return deepcopy(stored)

        run_metadata = self.run_metadata if isinstance(self.run_metadata, dict) else {}
        variant_values = run_metadata.get("variant_values") if isinstance(run_metadata.get("variant_values"), dict) else {}
        edge = run_metadata.get("edge") if isinstance(run_metadata.get("edge"), dict) else {}
        runtime_env_name = str(run_metadata.get("runtime_env_name") or "").strip()
        if not runtime_env_name:
            snapshot = self.compiled_payload_snapshot if isinstance(self.compiled_payload_snapshot, dict) else {}
            runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), list) else []
            first_run = runs[0] if runs and isinstance(runs[0], dict) else {}
            runtime_env_name = str(first_run.get("runtime_env_name") or "").strip()
            if not variant_values:
                variant_values = first_run.get("variant_values") if isinstance(first_run.get("variant_values"), dict) else {}
            if not edge:
                edge = first_run.get("edge") if isinstance(first_run.get("edge"), dict) else {}

        rrt_snapshot = self.rrt_config_execution_snapshot if isinstance(self.rrt_config_execution_snapshot, dict) else {}
        rrt_runs = rrt_snapshot.get("runs") if isinstance(rrt_snapshot.get("runs"), dict) else {}
        rrt_run = {}
        plan_key = str(self.plan_run_id or "").strip()
        if plan_key and isinstance(rrt_runs.get(plan_key), dict):
            rrt_run = rrt_runs.get(plan_key)
        elif rrt_runs:
            first_key = next(iter(rrt_runs))
            if isinstance(rrt_runs.get(first_key), dict):
                rrt_run = rrt_runs.get(first_key)
        rrt_payload = rrt_run.get("generated_rrt_config_json") if isinstance(rrt_run, dict) else {}

        return self.build_characterization_parameters(
            plan_run_id=self.plan_run_id,
            runtime_env_name=runtime_env_name,
            variant_values=variant_values,
            edge=edge,
            rrt_config_payload=rrt_payload if isinstance(rrt_payload, dict) else {},
        )

    @staticmethod
    def _parse_yaml_to_json(raw_yaml: str) -> Any:
        yaml_text = str(raw_yaml or "").strip()
        if not yaml_text:
            return {}
        try:
            parsed = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return {}
        if parsed is None:
            return {}
        if isinstance(parsed, (dict, list, str, int, float, bool)):
            return parsed
        return {}

    def get_rrt_config(self) -> list[dict[str, Any]]:
        """
        Return per-run runtime RRT config content in JSON form.

        Output rows:
          - run_id
          - runtime_rrt_config_path
          - rrt_config_json
        """
        snapshot = self.rrt_config_execution_snapshot if isinstance(self.rrt_config_execution_snapshot, dict) else {}
        snapshot_runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), dict) else {}
        results = self.results if isinstance(self.results, list) else []

        run_ids: list[str] = []
        for run_id in snapshot_runs.keys():
            text = str(run_id or "").strip()
            if text and text not in run_ids:
                run_ids.append(text)
        for result_item in results:
            if not isinstance(result_item, dict):
                continue
            run_id = str(result_item.get("run_id") or "").strip()
            if run_id and run_id not in run_ids:
                run_ids.append(run_id)

        output: list[dict[str, Any]] = []
        for run_id in run_ids:
            run_snapshot = snapshot_runs.get(run_id) if isinstance(snapshot_runs.get(run_id), dict) else {}
            runtime_path = str(run_snapshot.get("runtime_rrt_config_path") or "").strip()
            generated_backend_path = str(run_snapshot.get("generated_rrt_config_backend_path") or "").strip()
            generated_url = str(run_snapshot.get("generated_rrt_config_url") or "").strip()

            yaml_text = str(run_snapshot.get("generated_rrt_config_yaml") or "").strip()
            actions = run_snapshot.get("actions") if isinstance(run_snapshot.get("actions"), list) else []
            if not yaml_text:
                for action_item in reversed(actions):
                    if not isinstance(action_item, dict):
                        continue
                    candidate = str(action_item.get("rrt_config_yaml") or "").strip()
                    if candidate:
                        yaml_text = candidate
                        break

            if not yaml_text and runtime_path:
                path_obj = self._to_local_path(runtime_path)
                if path_obj.is_file():
                    try:
                        yaml_text = path_obj.read_text(encoding="utf-8")
                    except Exception:
                        yaml_text = ""

            if not generated_url and generated_backend_path:
                generated_path_obj = self._to_local_path(generated_backend_path)
                generated_url = self._to_database_url(generated_path_obj)

            output.append(
                {
                    "run_id": run_id,
                    "runtime_rrt_config_path": runtime_path,
                    "generated_rrt_config_backend_path": generated_backend_path,
                    "generated_rrt_config_url": generated_url,
                    "rrt_config_yaml": yaml_text,
                    "rrt_config_json": self._parse_yaml_to_json(yaml_text),
                }
            )
        return output

    @staticmethod
    def _parse_json_to_object(raw_text: str) -> dict[str, Any]:
        text = str(raw_text or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def get_rrt_summaries(self) -> list[dict[str, Any]]:
        """
        Return per-run rrt_summary.json content directly in API response.

        Output rows:
          - run_id
          - rrt_summary_json
        """
        output: list[dict[str, Any]] = []
        run_ids: list[str] = []
        for result_item in (self.results if isinstance(self.results, list) else []):
            if not isinstance(result_item, dict):
                continue
            run_id = str(result_item.get("run_id") or "").strip()
            if run_id and run_id not in run_ids:
                run_ids.append(run_id)

        for run_id in run_ids:
            summary_file = self.find_run_metric_file_path(run_id, "rrt_summary.json")
            if not isinstance(summary_file, Path) or not summary_file.is_file():
                continue
            try:
                summary_text = summary_file.read_text(encoding="utf-8")
            except Exception:
                continue
            output.append(
                {
                    "run_id": run_id,
                    "rrt_summary_json": self._parse_json_to_object(summary_text),
                }
            )
        return output


    @staticmethod
    def _safe_path_segment(value: str) -> str:
        text = str(value or "").strip().replace("/", "_")
        return text or "unknown"

    def get_experiment_result_backend_path(self) -> str:
        """Relative backend path for storing this run's collected metrics."""
        experiment_name = self._safe_path_segment(self.experiment.name)
        return (
            f"{_GENERATED_RESULT_BACKEND_BASE_DIR}/"
            f"{experiment_name}/run_{self.id}/metrics"
        )

    def get_experiment_result_target_host_path(self) -> str:
        """Target VM host path for writing probe metrics before collection."""
        experiment_name = self._safe_path_segment(self.experiment.name)
        return f"{_GENERATED_RESULT_TARGET_HOST_BASE_DIR}/{experiment_name}/run_{self.id}/metrics"

    def get_collected_metrics_url_in_database(self) -> str:
        """Relative URL path for browsing collected metrics in backend static files."""
        raw_path = str(self.collected_metrics_path or self.get_experiment_result_backend_path() or "").strip()
        if not raw_path:
            return ""
        normalized = raw_path.lstrip("/")
        if normalized.startswith("static/"):
            return f"/{normalized}"
        return f"/static/{normalized}"

    @staticmethod
    def _to_local_path(path_text: str) -> Path:
        raw = str(path_text or "").strip()
        if not raw:
            return Path()
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        return Path(settings.BASE_DIR) / raw.lstrip("/")

    @staticmethod
    def _to_database_url(path_obj: Path) -> str:
        try:
            relative = path_obj.resolve().relative_to(Path(settings.BASE_DIR).resolve())
        except Exception:
            return ""
        normalized = relative.as_posix().lstrip("/")
        if not normalized:
            return ""
        if normalized.startswith("static/"):
            return f"/{normalized}"
        return f"/static/{normalized}"

    @staticmethod
    def _find_metric_file(run_dir: Path, filename: str) -> Path | None:
        if not run_dir.is_dir():
            return None
        preferred = run_dir / "vm_home" / filename
        if preferred.is_file():
            return preferred
        matches = sorted(run_dir.rglob(filename))
        return matches[0] if matches else None

    def _candidate_run_dirs(self, run_id: str) -> list[Path]:
        run_key = str(run_id or "").strip()
        if not run_key:
            return []
        results = self.results if isinstance(self.results, list) else []
        collected_base = str(self.collected_metrics_path or self.get_experiment_result_backend_path() or "").strip()
        candidate_dirs: list[str] = []
        for result_item in results:
            if not isinstance(result_item, dict):
                continue
            if str(result_item.get("run_id") or "").strip() != run_key:
                continue
            result_dir = str(result_item.get("metrics_backend_dir") or "").strip()
            if result_dir:
                candidate_dirs.append(result_dir)
                break
        if collected_base:
            candidate_dirs.append(f"{collected_base.rstrip('/')}/{run_key}")

        seen: set[str] = set()
        output: list[Path] = []
        for text in candidate_dirs:
            local_dir = self._to_local_path(text)
            key = str(local_dir)
            if key in seen:
                continue
            seen.add(key)
            output.append(local_dir)
        return output

    def find_run_metric_file_path(self, run_id: str, filename: str) -> Path | None:
        run_key = str(run_id or "").strip()
        file_name = str(filename or "").strip()
        if not run_key or not file_name:
            return None
        for run_dir in self._candidate_run_dirs(run_key):
            found = self._find_metric_file(run_dir, file_name)
            if found:
                return found
        return None

    def get_run_metric_file_urls(self) -> list[dict[str, str]]:
        """Find per-run RRT artifact API URLs by searching backend metrics directories."""
        files_by_run: list[dict[str, str]] = []
        run_ids: list[str] = []
        for result_item in (self.results if isinstance(self.results, list) else []):
            if not isinstance(result_item, dict):
                continue
            run_id = str(result_item.get("run_id") or "").strip()
            if run_id and run_id not in run_ids:
                run_ids.append(run_id)

        for run_id in run_ids:
            summary_file = self.find_run_metric_file_path(run_id, "rrt_summary.json")
            config_file = self.find_run_metric_file_path(run_id, "rrt_config.yaml")
            all_csv_file = self.find_run_metric_file_path(run_id, "rrt_all.csv")
            if not summary_file and not config_file and not all_csv_file:
                continue

            row: dict[str, str] = {"run_id": run_id}
            if summary_file:
                row["rrt_summary_url"] = (
                    f"/profiling_exps/runs/{self.id}/metric_files/{run_id}/rrt_summary.json/"
                )
            if config_file:
                row["rrt_config_url"] = (
                    f"/profiling_exps/runs/{self.id}/metric_files/{run_id}/rrt_config.yaml/"
                )
            if all_csv_file:
                row["rrt_all_csv_url"] = (
                    f"/profiling_exps/runs/{self.id}/metric_files/{run_id}/rrt_all.csv/"
                )
            files_by_run.append(row)

        return files_by_run


class ProfilingComparision(models.Model):
    """Saved run-set for comparison visualization."""

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profiling_comparisions",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_profiling_comparisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("created_by", "name")
        indexes = [models.Index(fields=["created_by", "created_at"])]

    def __str__(self) -> str:
        return f"{self.name} (#{self.id})"


class ProfilingComparisionRun(models.Model):
    """One run reference in a saved comparison."""

    comparision = models.ForeignKey(
        ProfilingComparision,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    run = models.ForeignKey(
        ProfilingRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comparision_rows",
    )
    run_id_snapshot = models.PositiveIntegerField()
    experiment_name_snapshot = models.CharField(max_length=120, blank=True, default="")
    plan_run_id_snapshot = models.CharField(max_length=32, blank=True, default="")
    status_snapshot = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("comparision", "run_id_snapshot")
        indexes = [
            models.Index(fields=["comparision", "created_at"]),
            models.Index(fields=["run_id_snapshot"]),
        ]

    def __str__(self) -> str:
        return f"comparision#{self.comparision_id}:run#{self.run_id_snapshot}"

"""Serializers for scenario APIs."""

from rest_framework import serializers

from inventory.serializers import AuthenticatedRequestSerializer

from .models import Scenario, ScenarioValidationRun


class ScenarioSerializer(serializers.ModelSerializer):
    cached_yaml_file = serializers.FileField(read_only=True)
    graph_edges = serializers.SerializerMethodField()
    graph_edge_count = serializers.SerializerMethodField()
    last_node_check_summary = serializers.SerializerMethodField()
    last_graph_check_summary = serializers.SerializerMethodField()
    last_failure_report = serializers.SerializerMethodField()

    class Meta:
        model = Scenario
        fields = [
            "id",
            "name",
            "kind",
            "description",
            "metadata",
            "nodes",
            "graph",
            "runtime_env",
            "payloads",
            "background_workloads",
            "graph_edges",
            "graph_edge_count",
            "last_node_check_summary",
            "check_status_node",
            "check_status_graph",
            "check_status_actions",
            "validation_requested",
            "validation_status",
            "last_validation_task_id",
            "last_validation_at",
            "last_validation_data",
            "last_graph_check_summary",
            "last_failure_report",
            "validation_trace",
            "validation_history",
            "raw_payload",
            "raw_yaml",
            "cached_yaml_file",
            "created_at",
            "updated_at",
        ]

    def get_graph_edges(self, obj: Scenario):
        return obj.graph_edges()

    def get_graph_edge_count(self, obj: Scenario):
        return obj.graph_edge_count()

    def get_last_node_check_summary(self, obj: Scenario):
        payload = obj.last_validation_data if isinstance(obj.last_validation_data, dict) else {}
        summary = obj.get_node_check_summary(payload)
        summary["checked_at"] = (
            obj.last_validation_at.isoformat()
            if obj.last_validation_at
            and (
                summary.get("status") in {"PASS", "FAIL"}
                or int(summary.get("checked_nodes") or 0) > 0
            )
            else ""
        )
        if int(summary.get("checked_nodes") or 0) > 0:
            return summary

        run = (
            obj.validation_runs.filter(
                check_nodes=True,
                status__in=[
                    ScenarioValidationRun.RunStatus.SUCCEEDED,
                    ScenarioValidationRun.RunStatus.FAILED,
                    ScenarioValidationRun.RunStatus.CANCELED,
                ],
            )
            .order_by("-finished_at", "-requested_at")
            .first()
        )
        if run and isinstance(run.result_data, dict):
            fallback = obj.get_node_check_summary(run.result_data)
            fallback["checked_at"] = run.finished_at.isoformat() if run.finished_at else ""
            if int(fallback.get("checked_nodes") or 0) > 0:
                return fallback
        return summary

    def get_last_graph_check_summary(self, obj: Scenario):
        payload = obj.last_validation_data if isinstance(obj.last_validation_data, dict) else {}
        return obj.get_graph_check_summary(payload)

    def get_last_failure_report(self, obj: Scenario):
        payload = obj.last_validation_data if isinstance(obj.last_validation_data, dict) else {}
        existing = payload.get("failure_report")
        if isinstance(existing, dict):
            return existing
        return obj.build_failure_report(payload)


class ScenarioUploadSerializer(AuthenticatedRequestSerializer):
    file = serializers.FileField()
    force = serializers.BooleanField(required=False, default=False)

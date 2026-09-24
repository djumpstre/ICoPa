"""Serializers for profiling experiment APIs."""

from rest_framework import serializers

from inventory.serializers import AuthenticatedRequestSerializer

from .models import (
    ProfilingComparision,
    ProfilingComparisionRun,
    ProfilingExperiment,
    ProfilingRun,
)


class ProfilingExperimentSerializer(serializers.ModelSerializer):
    has_generated_plan = serializers.SerializerMethodField()
    execution_status = serializers.SerializerMethodField()

    def get_has_generated_plan(self, obj) -> bool:
        checker = getattr(obj, "has_generated_plan", None)
        if callable(checker):
            return bool(checker())
        return False

    def get_execution_status(self, obj) -> dict:
        resolver = getattr(obj, "get_current_execution_status", None)
        if callable(resolver):
            status = resolver()
            if isinstance(status, dict):
                return status
        return {}

    class Meta:
        model = ProfilingExperiment
        fields = [
            "id",
            "name",
            "kind",
            "description",
            "scenario_name",
            "execution",
            "selections",
            "phases",
            "generated_payload",
            "generated_yaml",
            "generated_source_refs",
            "generated_source_hashes",
            "total_generated_runs",
            "current_gen_version",
            "generated_at",
            "run_check_status",
            "run_check_report",
            "run_checked_at",
            "raw_payload",
            "raw_yaml",
            "status",
            "start_count",
            "last_started_at",
            "has_generated_plan",
            "execution_status",
            "created_at",
            "updated_at",
        ]


class ProfilingExperimentUploadSerializer(AuthenticatedRequestSerializer):
    file = serializers.FileField()


class ProfilingRunSerializer(serializers.ModelSerializer):
    experiment_name = serializers.CharField(source="experiment.name", read_only=True)
    characterization_parameters = serializers.SerializerMethodField()
    collected_metrics_url = serializers.SerializerMethodField()
    run_metric_files = serializers.SerializerMethodField()
    rrt_configs = serializers.SerializerMethodField()
    rrt_summaries = serializers.SerializerMethodField()

    def get_characterization_parameters(self, obj) -> dict:
        getter = getattr(obj, "get_characterization_parameters", None)
        if callable(getter):
            resolved = getter()
            if isinstance(resolved, dict):
                return resolved
        raw = getattr(obj, "characterization_parameters", None)
        return raw if isinstance(raw, dict) else {}

    def get_collected_metrics_url(self, obj) -> str:
        getter = getattr(obj, "get_collected_metrics_url_in_database", None)
        if callable(getter):
            return str(getter() or "")
        return ""

    def get_run_metric_files(self, obj) -> list[dict[str, str]]:
        if not bool(self.context.get("include_run_metric_files", False)):
            return []
        getter = getattr(obj, "get_run_metric_file_urls", None)
        if callable(getter):
            found = getter()
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
        return []

    def get_rrt_configs(self, obj) -> list[dict]:
        if not bool(self.context.get("include_run_metric_files", False)):
            return []
        getter = getattr(obj, "get_rrt_config", None)
        if callable(getter):
            found = getter()
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
        return []

    def get_rrt_summaries(self, obj) -> list[dict]:
        if not bool(self.context.get("include_run_metric_files", False)):
            return []
        getter = getattr(obj, "get_rrt_summaries", None)
        if callable(getter):
            found = getter()
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
        return []

    class Meta:
        model = ProfilingRun
        fields = [
            "id",
            "experiment_name",
            "status",
            "gen_version",
            "plan_run_id",
            "stop_on_failure",
            "total_runs",
            "completed_runs",
            "failed_runs",
            "run_metadata",
            "characterization_parameters",
            "compiled_payload_snapshot",
            "task_id",
            "results",
            "phase_action_execution_status",
            "rrt_config_execution_snapshot",
            "collected_metrics_path",
            "collected_metrics_url",
            "run_metric_files",
            "rrt_configs",
            "rrt_summaries",
            "metrics_collected",
            "note",
            "error_message",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ]


class ProfilingComparisionRunSerializer(serializers.ModelSerializer):
    run_id = serializers.SerializerMethodField()
    deleted = serializers.SerializerMethodField()
    experiment_name = serializers.SerializerMethodField()
    plan_run_id = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    def get_run_id(self, obj) -> int:
        run_obj = getattr(obj, "run", None)
        if isinstance(run_obj, ProfilingRun):
            return int(run_obj.id)
        return int(obj.run_id_snapshot)

    def get_deleted(self, obj) -> bool:
        run_obj = getattr(obj, "run", None)
        return not isinstance(run_obj, ProfilingRun)

    def get_experiment_name(self, obj) -> str:
        run_obj = getattr(obj, "run", None)
        if isinstance(run_obj, ProfilingRun) and isinstance(run_obj.experiment, ProfilingExperiment):
            return str(run_obj.experiment.name or "")
        return str(obj.experiment_name_snapshot or "")

    def get_plan_run_id(self, obj) -> str:
        run_obj = getattr(obj, "run", None)
        if isinstance(run_obj, ProfilingRun):
            return str(run_obj.plan_run_id or "")
        return str(obj.plan_run_id_snapshot or "")

    def get_status(self, obj) -> str:
        run_obj = getattr(obj, "run", None)
        if isinstance(run_obj, ProfilingRun):
            return str(run_obj.status or "")
        return str(obj.status_snapshot or "")

    class Meta:
        model = ProfilingComparisionRun
        fields = [
            "id",
            "run_id",
            "deleted",
            "experiment_name",
            "plan_run_id",
            "status",
            "created_at",
        ]


class ProfilingComparisionSerializer(serializers.ModelSerializer):
    runs = serializers.SerializerMethodField()
    run_count = serializers.SerializerMethodField()

    def get_runs(self, obj) -> list[dict]:
        queryset = (
            obj.runs.select_related("run", "run__experiment")
            .order_by("created_at", "id")
        )
        return ProfilingComparisionRunSerializer(queryset, many=True).data

    def get_run_count(self, obj) -> int:
        return int(obj.runs.count())

    class Meta:
        model = ProfilingComparision
        fields = [
            "id",
            "name",
            "description",
            "run_count",
            "runs",
            "created_at",
            "updated_at",
        ]


class ProfilingComparisionCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True)
    run_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)

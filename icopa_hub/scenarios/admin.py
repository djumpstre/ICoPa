from django.contrib import admin

from .models import Scenario, ScenarioValidationRun, ScenarioValidationRunVM


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "kind",
        "check_status_node",
        "check_status_graph",
        "check_status_actions",
        "validation_status",
        "created_by",
        "updated_at",
    )
    search_fields = ("name", "kind", "created_by__username")
    list_filter = ("validation_status", "check_status_node", "check_status_graph", "check_status_actions")
    list_select_related = ("created_by", "modified_by")


@admin.register(ScenarioValidationRun)
class ScenarioValidationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "scenario",
        "requested_by",
        "status",
        "phase_name",
        "requested_at",
        "started_at",
        "finished_at",
    )
    search_fields = ("scenario__name", "requested_by__username", "task_id")
    list_filter = ("status", "check_actions", "check_nodes", "check_graph", "requested_at")
    list_select_related = ("scenario", "requested_by")


@admin.register(ScenarioValidationRunVM)
class ScenarioValidationRunVMAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "run",
        "vm",
        "node_name",
        "phase_name",
        "collected",
        "created_at",
        "updated_at",
    )
    search_fields = ("run__scenario__name", "vm__name", "node_name", "phase_name")
    list_filter = ("collected", "created_at")
    list_select_related = ("run", "vm")

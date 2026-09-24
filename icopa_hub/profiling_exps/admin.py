from django.contrib import admin

from .models import ProfilingExperiment, ProfilingRun


class ProfilingRunInline(admin.TabularInline):
    model = ProfilingRun
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "id",
        "requested_by",
        "status",
        "total_runs",
        "completed_runs",
        "failed_runs",
        "started_at",
        "finished_at",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ProfilingExperiment)
class ProfilingExperimentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "scenario_name",
        "status",
        "total_generated_runs",
        "run_check_status",
        "start_count",
        "created_by",
        "updated_at",
    )
    search_fields = ("name", "scenario_name", "created_by__username")
    list_filter = ("status", "run_check_status")
    list_select_related = ("created_by", "modified_by")
    inlines = (ProfilingRunInline,)


@admin.register(ProfilingRun)
class ProfilingRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "experiment",
        "requested_by",
        "status",
        "total_runs",
        "completed_runs",
        "failed_runs",
        "created_at",
    )
    search_fields = ("experiment__name", "requested_by__username", "task_id")
    list_filter = ("status", "stop_on_failure", "created_at")
    list_select_related = ("experiment", "requested_by")

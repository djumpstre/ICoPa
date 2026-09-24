from django.contrib import admin

from .models import SSHAccessHistory, SSHCredential, VM


@admin.register(SSHCredential)
class SSHCredentialAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "key_path", "key_file", "user_name", "created_by", "created_at")
    search_fields = ("name", "key_path", "key_file", "user_name", "created_by__username")


@admin.register(VM)
class VMAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "group_name",
        "name",
        "address",
        "user_name",
        "port",
        "status",
        "credential",
        "created_by",
        "updated_at",
    )
    search_fields = ("group_name", "name", "address", "user_name", "created_by__username")
    list_filter = ("status", "container_runtime_ready")


@admin.register(SSHAccessHistory)
class SSHAccessHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "vm",
        "requested_by",
        "source",
        "scenario_name",
        "phase_name",
        "action_type",
        "success",
        "returncode",
        "created_at",
    )
    search_fields = (
        "vm__name",
        "requested_by__username",
        "scenario_name",
        "phase_name",
        "action_type",
        "command",
    )
    list_filter = ("source", "success", "created_at")
    list_select_related = ("vm", "requested_by")

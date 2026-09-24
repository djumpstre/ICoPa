from django.contrib import admin

from .models import RuntimeEnvironment


@admin.register(RuntimeEnvironment)
class RuntimeEnvironmentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "kind", "created_by", "updated_at")
    search_fields = ("name", "kind", "created_by__username")

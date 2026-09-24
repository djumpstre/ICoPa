from django.contrib import admin

from .models import ProvisioningVM, ProvisioningVMCheckRequest, ProvisioningVMEvent


@admin.register(ProvisioningVM)
class ProvisioningVMAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "provider", "status", "created_by", "created_at")
    search_fields = ("name", "provider", "status", "created_by__username")


@admin.register(ProvisioningVMEvent)
class ProvisioningVMEventAdmin(admin.ModelAdmin):
    list_display = ("id", "provisioning_vm", "level", "created_at")
    search_fields = ("provisioning_vm__name", "level", "message")


@admin.register(ProvisioningVMCheckRequest)
class ProvisioningVMCheckRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "provisioning_vm", "status", "created_by", "created_at")
    search_fields = ("provisioning_vm__name", "status", "created_by__username")

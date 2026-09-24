"""Inventory models for SSH credentials and VMs."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from settings.private_storage import PrivateCredentialStorage


class SSHCredential(models.Model):
    """SSH key metadata used to access a VM."""

    name = models.CharField(max_length=120)
    key_path = models.CharField(max_length=512)
    key_file = models.FileField(upload_to="inventory/ssh_keys/", storage=PrivateCredentialStorage(), blank=True, null=True)
    user_name = models.CharField(max_length=120, default="root")
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ssh_credentials",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("created_by", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.user_name})"


class VM(models.Model):
    """VM host entry imported from inventory YAML."""

    class VMStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unknown"
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        ERROR = "ERROR", "Error"

    name = models.CharField(max_length=120)
    group_name = models.CharField(max_length=120, default="default")
    address = models.CharField(max_length=255)
    user_name = models.CharField(max_length=120, default="root")
    port = models.PositiveIntegerField(default=22)
    description = models.TextField(blank=True)
    # Normalized host profile collected from VM checks, e.g. cpu/memory/gpu details.
    system_info = models.JSONField(default=dict, blank=True)
    # Running container names currently managed by ICoPa on this VM.
    managed_containers = models.JSONField(default=list, blank=True)
    # Stores host metadata and cached capability snapshots (cpu/mem/gpu) from preflight SSH probes.
    metadata = models.JSONField(default=dict, blank=True)
    networking = models.JSONField(default=dict, blank=True)
    auto_provisioned = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=VMStatus.choices, default=VMStatus.UNKNOWN)
    archived = models.BooleanField(default=False)
    container_runtime_ready = models.BooleanField(default=False)
    container_runtime_type = models.CharField(max_length=64, blank=True)
    cluster_membership = models.CharField(max_length=120, blank=True)
    last_connection_time = models.DateTimeField(null=True, blank=True)
    credential = models.ForeignKey(
        SSHCredential,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vms",
    )
    cloud_provisioning_vm = models.ForeignKey(
        "cloud_provisioning.ProvisioningVM",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inventory_vms",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vms",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_vms",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("created_by", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.address}:{self.port})"


class SSHAccessHistory(models.Model):
    """Audit record for SSH command executions triggered by orchestration tasks."""

    vm = models.ForeignKey(
        VM,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ssh_access_history",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ssh_access_history",
    )
    source = models.CharField(max_length=64, default="celery_task")
    scenario_name = models.CharField(max_length=120, blank=True)
    phase_name = models.CharField(max_length=120, blank=True)
    action_type = models.CharField(max_length=120, blank=True)
    command = models.TextField(blank=True, default="")
    success = models.BooleanField(default=False)
    returncode = models.IntegerField(default=0)
    stdout = models.TextField(blank=True, default="")
    stderr = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["source"]),
            models.Index(fields=["scenario_name", "phase_name"]),
        ]

    def __str__(self) -> str:
        vm_name = self.vm.name if self.vm else "unknown-vm"
        status = "ok" if self.success else "fail"
        return f"{vm_name} {self.action_type or 'ssh'} {status}"

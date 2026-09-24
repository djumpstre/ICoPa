"""Models for cloud VM provisioning lifecycle."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from inventory.models import SSHCredential, VM


class ProvisioningVM(models.Model):
    class Provider(models.TextChoices):
        AZURE = "AZURE", "Azure"

    class Status(models.TextChoices):
        INITIALIZED = "INITIALIZED", "Initialized"
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        DELETING = "DELETING", "Deleting"
        DELETED = "DELETED", "Deleted"

    name = models.CharField(max_length=120)
    group_name = models.CharField(max_length=120, default="default")
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.AZURE)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.INITIALIZED)
    task_id = models.CharField(max_length=120, blank=True)
    last_error = models.TextField(blank=True)
    raw_template = models.JSONField(default=dict, blank=True)
    request_spec = models.JSONField(default=dict, blank=True)
    icopa_config = models.JSONField(default=dict, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    instance_provider_name = models.CharField(max_length=64, blank=True, default="")
    instance_location = models.CharField(max_length=64, blank=True, default="")
    instance_size = models.CharField(max_length=64, blank=True, default="")
    instance_power_state = models.CharField(max_length=64, blank=True, default="")
    instance_public_ip = models.CharField(max_length=128, blank=True, default="")
    instance_nic_name = models.CharField(max_length=255, blank=True, default="")
    ssh_credential = models.ForeignKey(
        SSHCredential,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provisioning_vms",
    )
    inventory_vm = models.ForeignKey(
        VM,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cloud_provisioning_vms",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provisioning_vms",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_provisioning_vms",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("created_by", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.provider}:{self.status})"

    @property
    def vm_name(self) -> str:
        spec = self.request_spec if isinstance(self.request_spec, dict) else {}
        return str(spec.get("vm_name") or self.name)

    def update_execution_status(
        self,
        *,
        status: str,
        modified_by_id: int | None = None,
        last_error: str | None = None,
    ) -> None:
        self.status = status
        if modified_by_id is not None:
            self.modified_by_id = modified_by_id
        if last_error is not None:
            self.last_error = last_error
        self.save(update_fields=["status", "modified_by", "last_error", "updated_at"])

    def update_instance_snapshot(
        self,
        *,
        provider_name: str | None = None,
        location: str | None = None,
        size: str | None = None,
        power_state: str | None = None,
        public_ip: str | None = None,
        nic_name: str | None = None,
    ) -> None:
        if provider_name is not None:
            self.instance_provider_name = provider_name
        if location is not None:
            self.instance_location = location
        if size is not None:
            self.instance_size = size
        if power_state is not None:
            self.instance_power_state = power_state
        if public_ip is not None:
            self.instance_public_ip = public_ip
        if nic_name is not None:
            self.instance_nic_name = nic_name
        self.save(
            update_fields=[
                "instance_provider_name",
                "instance_location",
                "instance_size",
                "instance_power_state",
                "instance_public_ip",
                "instance_nic_name",
                "updated_at",
            ]
        )


class ProvisioningVMEvent(models.Model):
    provisioning_vm = models.ForeignKey(
        ProvisioningVM,
        on_delete=models.CASCADE,
        related_name="events",
    )
    level = models.CharField(max_length=16, default="INFO")
    message = models.TextField()
    event_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.provisioning_vm_id}:{self.level}"


class ProvisioningVMCheckRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"

    provisioning_vm = models.ForeignKey(
        ProvisioningVM,
        on_delete=models.CASCADE,
        related_name="check_requests",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    task_id = models.CharField(max_length=120, blank=True)
    check_data = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provisioning_vm_checks",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_provisioning_vm_checks",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.provisioning_vm_id}:{self.status}"

    def update_check_status(
        self,
        *,
        status: str,
        modified_by_id: int | None = None,
        last_error: str | None = None,
        check_data: dict | None = None,
    ) -> None:
        self.status = status
        if modified_by_id is not None:
            self.modified_by_id = modified_by_id
        if last_error is not None:
            self.last_error = last_error
        if check_data is not None:
            self.check_data = check_data
        self.save(update_fields=["status", "modified_by", "last_error", "check_data", "updated_at"])


class ProvisioningVMStopRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"

    provisioning_vm = models.ForeignKey(
        ProvisioningVM,
        on_delete=models.CASCADE,
        related_name="stop_requests",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    task_id = models.CharField(max_length=120, blank=True)
    stop_data = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provisioning_vm_stops",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_provisioning_vm_stops",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.provisioning_vm_id}:{self.status}"

    def update_stop_status(
        self,
        *,
        status: str,
        modified_by_id: int | None = None,
        last_error: str | None = None,
        stop_data: dict | None = None,
    ) -> None:
        self.status = status
        if modified_by_id is not None:
            self.modified_by_id = modified_by_id
        if last_error is not None:
            self.last_error = last_error
        if stop_data is not None:
            self.stop_data = stop_data
        self.save(update_fields=["status", "modified_by", "last_error", "stop_data", "updated_at"])


class ProvisioningVMStartRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"

    provisioning_vm = models.ForeignKey(
        ProvisioningVM,
        on_delete=models.CASCADE,
        related_name="start_requests",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    task_id = models.CharField(max_length=120, blank=True)
    start_data = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="provisioning_vm_starts",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_provisioning_vm_starts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.provisioning_vm_id}:{self.status}"

    def update_start_status(
        self,
        *,
        status: str,
        modified_by_id: int | None = None,
        last_error: str | None = None,
        start_data: dict | None = None,
    ) -> None:
        self.status = status
        if modified_by_id is not None:
            self.modified_by_id = modified_by_id
        if last_error is not None:
            self.last_error = last_error
        if start_data is not None:
            self.start_data = start_data
        self.save(update_fields=["status", "modified_by", "last_error", "start_data", "updated_at"])

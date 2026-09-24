"""Bounded online latency sessions and their measurement windows."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProbeSession(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        RUNNING = "RUNNING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"
        STOPPED = "STOPPED"

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source_vm = models.ForeignKey("inventory.VM", on_delete=models.PROTECT, related_name="outgoing_probes")
    target_vm = models.ForeignKey("inventory.VM", on_delete=models.PROTECT, related_name="incoming_probes")
    duration_sec = models.PositiveIntegerField(default=5)
    interval_sec = models.PositiveIntegerField(default=10)
    window_count = models.PositiveIntegerField(default=6)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    task_id = models.CharField(max_length=120, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["created_by", "source_vm", "target_vm"],
                condition=models.Q(status__in=["PENDING", "RUNNING"]),
                name="unique_active_probe_pair",
            ),
            models.CheckConstraint(condition=models.Q(duration_sec__gte=1, duration_sec__lte=10), name="probe_duration_bounds"),
            models.CheckConstraint(condition=models.Q(interval_sec__gte=1, interval_sec__lte=30), name="probe_interval_bounds"),
            models.CheckConstraint(condition=models.Q(window_count__gte=1, window_count__lte=60), name="probe_window_bounds"),
        ]

    def finish(self, status, error=""):
        if status not in {self.Status.SUCCEEDED, self.Status.FAILED, self.Status.STOPPED}:
            raise ValueError("Expected a terminal probe status.")
        changed = type(self).objects.filter(pk=self.pk, status__in=[self.Status.PENDING, self.Status.RUNNING]).update(
            status=status, error_message=error, finished_at=timezone.now(),
        )
        self.refresh_from_db()
        return bool(changed)


class ProbeWindow(models.Model):
    session = models.ForeignKey(ProbeSession, on_delete=models.CASCADE, related_name="windows")
    sequence = models.PositiveIntegerField()
    measured_at = models.DateTimeField(default=timezone.now)
    source_address = models.CharField(max_length=255)
    target_address = models.CharField(max_length=255)
    metrics = models.JSONField(default=dict)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["session", "sequence"], name="unique_probe_window")]

"""Models for runtime environment profiles."""

from __future__ import annotations

import re

from django.conf import settings
from django.db import models
import yaml


def _safe_runtime_name(value: str) -> str:
    text = str(value or "").strip().replace("/", "_")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-.")
    return text or "runtime"


def _runtime_yaml_upload_to(instance: "RuntimeEnvironment", _original_filename: str) -> str:
    runtime_name = _safe_runtime_name(instance.name)
    version = int(instance.uploaded_version or 0)
    if version <= 0:
        version = 0
    return f"static/runtime/{runtime_name}/{version}/runtime_env.yaml"


def _rrt_config_upload_to(instance: "RuntimeEnvironment", _original_filename: str) -> str:
    runtime_name = _safe_runtime_name(instance.name)
    version = int(instance.uploaded_version or 0)
    if version <= 0:
        version = 0
    return f"static/runtime/{runtime_name}/{version}/serializer_rrt_config.yaml"


class RuntimeEnvironment(models.Model):
    """Runtime environment imported from YAML."""

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=64, default="RuntimeEnvironment")
    metadata = models.JSONField(default=dict, blank=True)
    images = models.JSONField(default=dict, blank=True)
    tags = models.JSONField(default=dict, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    command_preset = models.JSONField(default=list, blank=True)
    command_groups = models.JSONField(default=dict, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    raw_yaml = models.TextField(blank=True, default="")
    cached_yaml_file = models.FileField(
        upload_to=_runtime_yaml_upload_to,
        blank=True,
        null=True,
    )
    serializer_rrt_config_file = models.FileField(
        upload_to=_rrt_config_upload_to,
        blank=True,
        null=True,
    )
    serializer_rrt_config_raw_yaml = models.TextField(blank=True, default="")
    serializer_rrt_config_path = models.CharField(max_length=512, blank=True, default="")
    uploaded_version = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="runtime_environments",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_runtime_environments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("created_by", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"

    def get_serializer_rrt_config_url_in_database(self) -> str:
        """Relative URL path for serializer_rrt_config YAML in backend static files."""
        raw_path = str(self.serializer_rrt_config_file.name or self.serializer_rrt_config_path or "").strip()
        if not raw_path:
            return ""
        if raw_path.startswith("http://") or raw_path.startswith("https://"):
            return raw_path
        normalized = raw_path.lstrip("/")
        if normalized.startswith("static/"):
            return f"/{normalized}"
        return f"/static/{normalized}"

    def get_serializer_rrt_config_json(self):
        """Parsed serializer_rrt_config YAML as JSON-serializable data."""
        raw_yaml = str(self.serializer_rrt_config_raw_yaml or "").strip()
        if not raw_yaml:
            return {}
        try:
            parsed = yaml.safe_load(raw_yaml)
        except yaml.YAMLError:
            return {}
        if parsed is None:
            return {}
        if isinstance(parsed, (dict, list, str, int, float, bool)):
            return parsed
        return {}

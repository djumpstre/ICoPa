"""Serializers for runtime environment APIs."""

from rest_framework import serializers

from inventory.serializers import AuthenticatedRequestSerializer

from .models import RuntimeEnvironment


class RuntimeEnvironmentSerializer(serializers.ModelSerializer):
    cached_yaml_file = serializers.FileField(read_only=True)
    serializer_rrt_config_file = serializers.FileField(read_only=True)
    serializer_rrt_config_url = serializers.SerializerMethodField()
    serializer_rrt_config_json = serializers.SerializerMethodField()

    def get_serializer_rrt_config_url(self, obj) -> str:
        getter = getattr(obj, "get_serializer_rrt_config_url_in_database", None)
        if callable(getter):
            return str(getter() or "")
        return ""

    def get_serializer_rrt_config_json(self, obj):
        getter = getattr(obj, "get_serializer_rrt_config_json", None)
        if callable(getter):
            return getter() or {}
        return {}

    class Meta:
        model = RuntimeEnvironment
        fields = [
            "id",
            "name",
            "kind",
            "metadata",
            "images",
            "tags",
            "parameters",
            "command_preset",
            "command_groups",
            "raw_payload",
            "raw_yaml",
            "cached_yaml_file",
            "serializer_rrt_config_file",
            "serializer_rrt_config_url",
            "serializer_rrt_config_raw_yaml",
            "serializer_rrt_config_json",
            "serializer_rrt_config_path",
            "uploaded_version",
            "created_at",
            "updated_at",
        ]


class RuntimeEnvironmentUploadSerializer(AuthenticatedRequestSerializer):
    file = serializers.FileField()
    rrt_config_file = serializers.FileField(required=False, allow_null=True)

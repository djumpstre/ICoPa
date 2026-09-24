"""Serializers for inventory APIs."""

from rest_framework import serializers

from .models import SSHCredential, VM


class SSHCredentialSerializer(serializers.ModelSerializer):
    key_file = serializers.FileField(use_url=False, read_only=True)

    class Meta:
        model = SSHCredential
        fields = ["id", "name", "key_path", "key_file", "user_name", "description", "created_at"]


class VMSerializer(serializers.ModelSerializer):
    credential = SSHCredentialSerializer(read_only=True)

    class Meta:
        model = VM
        fields = [
            "id",
            "name",
            "group_name",
            "address",
            "user_name",
            "port",
            "description",
            "system_info",
            "managed_containers",
            "status",
            "archived",
            "container_runtime_ready",
            "container_runtime_type",
            "cluster_membership",
            "last_connection_time",
            "credential",
            "metadata",
            "networking",
            "auto_provisioned",
            "cloud_provisioning_vm",
            "created_at",
            "updated_at",
        ]


class AuthenticatedRequestSerializer(serializers.Serializer):
    """Serializer base that requires an authenticated request user."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if request is None or user is None or not user.is_authenticated:
            raise serializers.ValidationError("Authentication credentials were not provided.")
        attrs["user"] = user
        return attrs


class InventoryUploadSerializer(AuthenticatedRequestSerializer):
    file = serializers.FileField()


class VMConnectivityCheckSerializer(AuthenticatedRequestSerializer):
    name = serializers.CharField()


class VMStopContainerSerializer(AuthenticatedRequestSerializer):
    vm_id = serializers.IntegerField(required=False)
    vm = serializers.CharField(required=False, allow_blank=False)
    task_id = serializers.CharField(required=False, allow_blank=False)
    container = serializers.RegexField(
        regex=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        allow_blank=False,
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        vm_id = attrs.get("vm_id")
        vm_name = attrs.get("vm")
        if vm_id is None and not vm_name:
            raise serializers.ValidationError("Provide either vm_id or vm.")
        if vm_id is not None and vm_name:
            raise serializers.ValidationError("Provide either vm_id or vm, not both.")
        return attrs

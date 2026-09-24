"""Serializers for cloud provisioning APIs."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from rest_framework import serializers
import yaml

from inventory.models import SSHCredential

from .models import (
    ProvisioningVM,
    ProvisioningVMCheckRequest,
    ProvisioningVMEvent,
    ProvisioningVMStartRequest,
    ProvisioningVMStopRequest,
)


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


class ProvisioningVMUploadSerializer(AuthenticatedRequestSerializer):
    file = serializers.FileField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        uploaded = attrs["file"]
        try:
            raw_text = uploaded.read().decode("utf-8")
        except Exception as exc:
            raise serializers.ValidationError({"file": "Failed to read uploaded YAML file."}) from exc
        try:
            payload = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as exc:
            raise serializers.ValidationError({"file": "Invalid YAML content."}) from exc
        if not isinstance(payload, dict):
            raise serializers.ValidationError({"file": "YAML root must be an object."})

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        group_name = str(metadata.get("group_name") or "default").strip() or "default"
        provider_raw = str(metadata.get("cloud_provider") or metadata.get("provider") or "").strip()
        if not provider_raw:
            raise serializers.ValidationError({"file": "metadata.cloud_provider is required."})
        provider = provider_raw.upper()
        if provider != ProvisioningVM.Provider.AZURE:
            raise serializers.ValidationError({"provider": f"Provider '{provider}' will support soon."})
        attrs["provider"] = provider
        attrs["group_name"] = group_name

        vm_entries = payload.get("vms")
        if not isinstance(vm_entries, list) or not vm_entries:
            raise serializers.ValidationError({"file": "YAML must define a non-empty 'vms' list."})

        parsed_specs: list[dict] = []
        parsed_ssh_credentials: list[SSHCredential] = []
        for index, vm in enumerate(vm_entries):
            if not isinstance(vm, dict):
                raise serializers.ValidationError({"file": f"vms[{index}] must be an object."})
            vm_name = str(vm.get("name") or "").strip()
            if not vm_name:
                raise serializers.ValidationError({"file": f"vms[{index}].name is required."})
            ssh_private_key_name = str(vm.get("ssh_private_key_name_in_icopa") or "").strip()
            if not ssh_private_key_name:
                raise serializers.ValidationError({"file": f"vms[{index}].ssh_private_key_name_in_icopa is required."})
            ssh_credential = SSHCredential.objects.filter(
                created_by=attrs["user"],
                name=ssh_private_key_name,
            ).first()
            if ssh_credential is None:
                raise serializers.ValidationError(
                    {
                        "file": (
                            f"vms[{index}] references unknown SSH key '{ssh_private_key_name}'. "
                            "Create/import this SSH key in inventory first."
                        )
                    }
                )

            ssh_public_key = str(vm.get("ssh_public_key") or "").strip()
            ssh_public_key_path = str(vm.get("ssh_public_key_path") or "").strip()
            if not ssh_public_key and ssh_public_key_path:
                key_path = Path(ssh_public_key_path)
                if not key_path.is_absolute():
                    key_path = Path(settings.BASE_DIR).parent / key_path
                if not key_path.exists():
                    raise serializers.ValidationError({"file": f"vms[{index}].ssh_public_key_path not found: {ssh_public_key_path}"})
                ssh_public_key = key_path.read_text(encoding="utf-8").strip()

            request_spec = {
                "subscription_selector": str(vm.get("subscription_selector") or vm.get("subscription_id") or "").strip(),
                "resource_group": str(vm.get("resource_group") or "").strip(),
                "group_name": group_name,
                "location": str(vm.get("location") or vm.get("region") or "").strip(),
                "vm_name": vm_name,
                "vm_size": str(vm.get("vm_size") or vm.get("size") or "").strip(),
                "admin_username": str(vm.get("admin_username") or "ubuntu").strip(),
                "ssh_private_key_name_in_icopa": ssh_private_key_name,
                "ssh_public_key": ssh_public_key,
                "image": vm.get("image") or {},
                "network": vm.get("network") or {},
                "spot": vm.get("spot") or {},
                "storage": vm.get("storage") or {},
                "tags": (payload.get("metadata") or {}).get("labels") or {},
                "custom_data": str(vm.get("custom_data") or ""),
            }
            icopa_config = vm.get("icopa_config")
            if not isinstance(icopa_config, dict):
                icopa_config = vm.get("icopa")
            # Compatibility with templates that provide the networking block directly.
            if not isinstance(icopa_config, dict):
                direct_networking = vm.get("networking")
                if isinstance(direct_networking, dict):
                    icopa_config = {"networking": direct_networking}
                else:
                    icopa_config = {}
            request_spec["icopa_config"] = icopa_config
            required_fields = ["resource_group", "location", "vm_size", "admin_username", "ssh_public_key"]
            missing = [name for name in required_fields if not request_spec.get(name)]
            if missing:
                raise serializers.ValidationError({"file": f"vms[{index}] missing fields: {', '.join(missing)}"})
            if not isinstance(request_spec["image"], dict):
                raise serializers.ValidationError({"file": f"vms[{index}].image must be an object."})
            for image_key in ("publisher", "offer", "sku"):
                if not request_spec["image"].get(image_key):
                    raise serializers.ValidationError({"file": f"vms[{index}].image.{image_key} is required."})
            if not str(request_spec["ssh_public_key"]).startswith("ssh-"):
                raise serializers.ValidationError({"file": f"vms[{index}].ssh_public_key format is invalid."})
            if not isinstance(request_spec["network"], dict):
                raise serializers.ValidationError({"file": f"vms[{index}].network must be an object."})
            if not isinstance(request_spec["spot"], dict):
                raise serializers.ValidationError({"file": f"vms[{index}].spot must be an object when provided."})
            if not isinstance(request_spec["storage"], dict):
                raise serializers.ValidationError({"file": f"vms[{index}].storage must be an object when provided."})
            if not isinstance(request_spec["icopa_config"], dict):
                raise serializers.ValidationError({"file": f"vms[{index}].icopa_config must be an object when provided."})
            if not isinstance(request_spec["tags"], dict):
                raise serializers.ValidationError({"file": "metadata.labels must be an object when provided."})
            parsed_specs.append(request_spec)
            parsed_ssh_credentials.append(ssh_credential)

        vm_names = [str(spec.get("vm_name") or "").strip() for spec in parsed_specs]
        duplicate_names: list[str] = []
        seen_names: set[str] = set()
        for vm_name in vm_names:
            if vm_name in seen_names and vm_name not in duplicate_names:
                duplicate_names.append(vm_name)
            seen_names.add(vm_name)
        if duplicate_names:
            joined = ", ".join(sorted(duplicate_names))
            raise serializers.ValidationError(
                {"file": f"Duplicate vm names in upload for group '{group_name}': {joined}"}
            )

        existing_names = sorted(
            ProvisioningVM.objects.filter(created_by=attrs["user"], name__in=vm_names)
            .values_list("name", flat=True)
            .distinct()
        )
        if existing_names:
            joined = ", ".join(existing_names)
            raise serializers.ValidationError(
                {
                    "file": (
                        f"VM names already exist for group '{group_name}': {joined}. "
                        "Delete existing records or rename VMs in template."
                    )
                }
            )

        attrs["raw_template"] = payload
        attrs["parsed_specs"] = parsed_specs
        attrs["parsed_ssh_credentials"] = parsed_ssh_credentials
        return attrs


class ProvisioningVMEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProvisioningVMEvent
        fields = ["id", "level", "message", "event_data", "created_at"]


class ProvisioningVMCheckRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProvisioningVMCheckRequest
        fields = ["id", "status", "task_id", "check_data", "last_error", "created_at", "updated_at"]


class ProvisioningVMStopRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProvisioningVMStopRequest
        fields = ["id", "status", "task_id", "stop_data", "last_error", "created_at", "updated_at"]


class ProvisioningVMStartRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProvisioningVMStartRequest
        fields = ["id", "status", "task_id", "start_data", "last_error", "created_at", "updated_at"]


class ProvisioningVMSerializer(serializers.ModelSerializer):
    ssh_credential = serializers.SerializerMethodField()
    inventory_vm = serializers.SerializerMethodField()
    events = ProvisioningVMEventSerializer(many=True, read_only=True)
    check_requests = ProvisioningVMCheckRequestSerializer(many=True, read_only=True)
    stop_requests = ProvisioningVMStopRequestSerializer(many=True, read_only=True)
    start_requests = ProvisioningVMStartRequestSerializer(many=True, read_only=True)

    class Meta:
        model = ProvisioningVM
        fields = [
            "id",
            "name",
            "group_name",
            "provider",
            "instance_provider_name",
            "instance_location",
            "instance_size",
            "instance_power_state",
            "instance_public_ip",
            "instance_nic_name",
            "status",
            "task_id",
            "last_error",
            "raw_template",
            "request_spec",
            "icopa_config",
            "result_data",
            "ssh_credential",
            "inventory_vm",
            "events",
            "check_requests",
            "stop_requests",
            "start_requests",
            "created_at",
            "updated_at",
        ]

    def get_ssh_credential(self, obj: ProvisioningVM):
        cred = obj.ssh_credential
        if cred is None:
            return None
        return {
            "id": cred.id,
            "name": cred.name,
            "user_name": cred.user_name,
            "key_path": cred.key_path,
        }

    def get_inventory_vm(self, obj: ProvisioningVM):
        vm = obj.inventory_vm
        if vm is None:
            return None
        return {
            "id": vm.id,
            "name": vm.name,
            "address": vm.address,
            "user_name": vm.user_name,
            "status": vm.status,
            "archived": vm.archived,
        }

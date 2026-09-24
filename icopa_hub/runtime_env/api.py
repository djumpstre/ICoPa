"""APIs for runtime environment management."""

from __future__ import annotations

from django.core.files.base import ContentFile
from pathlib import Path
from typing import Any

from django.conf import settings
from icopa_core.task_executor.action_center import action_catalog_entries
from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import load_preset_containers
from icopa_core.task_executor.vm_runtime_actions.vm_zenoh_routing_setup import (
    builtin_zenoh_routing_preset_names,
)
import yaml
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import RuntimeEnvironment
from .serializers import RuntimeEnvironmentSerializer, RuntimeEnvironmentUploadSerializer

_ZENOH_ALLOWED_PROFILING_PRESET_NAMES = {"latency_responder", "latency_sender"}


def _collect_field_updates(obj, new_values: dict) -> dict:
    changed_fields = {}
    for field_name, new_value in new_values.items():
        old_value = getattr(obj, field_name)
        if old_value != new_value:
            changed_fields[field_name] = {"old": old_value, "new": new_value}
    return changed_fields


def _is_zenoh_runtime(tags: dict[str, Any]) -> bool:
    zenoh = tags.get("zenoh")
    if not isinstance(zenoh, dict):
        return False
    return bool(zenoh) or zenoh.get("enabled") is True


def _normalize_zenoh_profiling_presets(command_presets: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for index, raw_item in enumerate(command_presets):
        if not isinstance(raw_item, dict):
            raise ValidationError(
                {"commandPresets": f"commandPresets[{index}] must be an object for zenoh runtime environments."}
            )

        item = dict(raw_item)
        group = str(item.get("group") or "").strip()
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValidationError(
                {"commandPresets": f"commandPresets[{index}] requires non-empty field 'name'."}
            )
        if "/" in name:
            inferred_group, inferred_name = name.split("/", 1)
            if not group:
                group = inferred_group.strip()
            name = inferred_name.strip()

        if group and group != "profiling":
            raise ValidationError(
                {
                    "commandPresets": (
                        "Zenoh runtime environments only allow profiling presets "
                        "(latency_responder, latency_sender). Routing presets are built-in."
                    )
                }
            )
        if name not in _ZENOH_ALLOWED_PROFILING_PRESET_NAMES:
            raise ValidationError(
                {
                    "commandPresets": (
                        "Zenoh runtime environments only allow presets "
                        "'latency_responder' and 'latency_sender'."
                    )
                }
            )

        item["group"] = "profiling"
        item["name"] = name
        normalized.append(item)
        seen_names.add(name)

    if seen_names != _ZENOH_ALLOWED_PROFILING_PRESET_NAMES:
        missing = sorted(_ZENOH_ALLOWED_PROFILING_PRESET_NAMES - seen_names)
        raise ValidationError(
            {"commandPresets": f"Zenoh runtime requires both profiling presets: {', '.join(missing)}."}
        )
    if len(normalized) != 2:
        raise ValidationError(
            {
                "commandPresets": (
                    "Zenoh runtime environments must define exactly two profiling presets: "
                    "latency_responder and latency_sender."
                )
            }
        )
    return normalized


def _extract_runtime_payload(payload: dict) -> tuple[str, str, dict, dict, dict, dict, list, dict]:
    if not isinstance(payload, dict):
        raise ValidationError({"file": "Runtime environment YAML root must be an object."})

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValidationError({"metadata": "metadata must be an object when provided."})

    spec = payload.get("spec")
    if "spec" in payload and spec is not None and not isinstance(spec, dict):
        raise ValidationError({"spec": "spec must be an object when provided."})
    body = spec if isinstance(spec, dict) else payload

    name = metadata.get("name") or payload.get("name")
    if not name:
        raise ValidationError({"metadata": "metadata.name is required."})

    kind = payload.get("kind") or "RuntimeEnvironment"
    if kind != "RuntimeEnvironment":
        raise ValidationError(
            {"kind": f"Expected kind 'RuntimeEnvironment', got '{kind}'."}
        )

    images = body.get("images") or {}
    tags = metadata.get("tags")
    if tags in (None, ""):
        tags = body.get("tags") or {}
    parameters = body.get("parameters") or body.get("generalParameters") or {}
    runtime_parameters = body.get("runtimeParameters") or {}
    if runtime_parameters:
        if not isinstance(runtime_parameters, dict):
            raise ValidationError({"runtimeParameters": "runtimeParameters must be an object."})
        merged_parameters = dict(parameters)
        merged_parameters["runtimeParameters"] = runtime_parameters
        parameters = merged_parameters
    deprecated_keys = [
        "commandPreset",
        "command_presets",
        "commandGroups",
        "command_groups",
        "profilingCommandGroups",
        "profiling_command_groups",
        "commandPresetsForRouting",
        "command_presets_for_routing",
        "commandPresetsTrafficGenerator",
        "command_presets_traffic_generator",
    ]
    for key in deprecated_keys:
        if key in body:
            raise ValidationError(
                {
                    key: (
                        f"spec.{key} is not supported. Use spec.commandPresets (flat list with group/name/executor/command)."
                    )
                }
            )

    if "commandPresets" not in body:
        raise ValidationError({"commandPresets": "spec.commandPresets is required (use [] when empty)."})
    command_presets = body.get("commandPresets")
    command_groups: dict[str, list[dict[str, Any]]] = {}

    if not isinstance(images, dict):
        raise ValidationError({"images": "images must be an object."})
    if "runnerImage" in images:
        runner_image = images["runnerImage"]
        if not isinstance(runner_image, str) or not runner_image.strip():
            raise ValidationError({
                "images": {
                    "runnerImage": "Set spec.images.runnerImage to your container image reference before upload."
                }
            })
        images["runnerImage"] = runner_image.strip()
    if not isinstance(tags, dict):
        raise ValidationError({"tags": "tags must be an object."})
    if not isinstance(parameters, dict):
        raise ValidationError({"parameters": "parameters must be an object."})
    if not isinstance(command_presets, list):
        raise ValidationError({"commandPresets": "commandPresets must be a list."})

    normalized_command_presets: list[dict[str, Any]] = []
    for index, item in enumerate(command_presets):
        if not isinstance(item, dict):
            raise ValidationError({"commandPresets": f"commandPresets[{index}] must be an object."})
        group = str(item.get("group") or "").strip()
        preset_name = str(item.get("name") or "").strip()
        executor = str(item.get("executor") or "").strip()
        command = str(item.get("command") or "").strip()
        if not group:
            raise ValidationError({"commandPresets": f"commandPresets[{index}].group is required."})
        if not preset_name:
            raise ValidationError({"commandPresets": f"commandPresets[{index}].name is required."})
        if not executor:
            raise ValidationError({"commandPresets": f"commandPresets[{index}].executor is required."})
        if not command:
            raise ValidationError({"commandPresets": f"commandPresets[{index}].command is required."})

        normalized_item = dict(item)
        normalized_item["group"] = group
        normalized_item["name"] = preset_name
        normalized_item["executor"] = executor
        normalized_item["command"] = command
        normalized_command_presets.append(normalized_item)
        command_groups.setdefault(group, []).append(normalized_item)
    command_presets = normalized_command_presets

    if _is_zenoh_runtime(tags):
        invalid_group_keys = [key for key in command_groups.keys() if str(key) != "profiling"]
        if invalid_group_keys:
            raise ValidationError(
                {
                    "commandGroups": (
                        "Zenoh runtime environments only allow profiling command groups. "
                        "Routing setup is built-in."
                    )
                }
            )
        command_presets = _normalize_zenoh_profiling_presets(command_presets)
        command_groups = {
            "profiling": [item for item in command_presets if str(item.get("group") or "") == "profiling"]
        }

    return name, kind, metadata, images, tags, parameters, command_presets, command_groups


def _extract_rrt_config_ref(payload: dict[str, Any]) -> str:
    spec = payload.get("spec")
    body = spec if isinstance(spec, dict) else payload
    params = body.get("parameters") or {}
    if not isinstance(params, dict):
        return ""
    return str(
        params.get("rrt_config_file_path")
        or params.get("rrtConfigFilePath")
        or ""
    ).strip()


def _load_rrt_config_file_from_payload_ref(payload: dict[str, Any]) -> tuple[bytes, str] | tuple[None, None]:
    raw_ref = _extract_rrt_config_ref(payload)
    if not raw_ref:
        return None, None
    ref_path = Path(raw_ref)
    candidates: list[Path] = []
    if ref_path.is_absolute():
        candidates.append(ref_path)
    else:
        workspace_root = Path(settings.BASE_DIR).parent
        candidates.append(workspace_root / ref_path)
        candidates.append(Path(settings.BASE_DIR) / ref_path)
        candidates.append(Path.cwd() / ref_path)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            raw_bytes = candidate.read_bytes()
            return raw_bytes, candidate.name
    raise ValidationError(
        {
            "rrt_config_file": (
                f"Failed to resolve rrt config file from runtime parameter 'rrt_config_file_path': {raw_ref}"
            )
        }
    )


def _runtime_presets_response(runtime_env: RuntimeEnvironment | None) -> list[dict[str, Any]]:
    if runtime_env is None or not isinstance(runtime_env.command_preset, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in runtime_env.command_preset:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "group": str(item.get("group") or ""),
                "name": str(item.get("name") or ""),
                "executor": str(item.get("executor") or ""),
                "source": "runtime_env",
            }
        )
    return rows


def _builtin_presets_response() -> dict[str, list[dict[str, Any]]]:
    stress_rows: list[dict[str, Any]] = []
    for preset in load_preset_containers():
        stress_rows.append(
            {
                "group": "stress",
                "name": f"stress/{preset.preset_id}",
                "description": str(preset.description or ""),
                "image_configured": bool(preset.image),
                "source": "builtin",
            }
        )
    routing_rows = [
        {
            "group": "routing",
            "name": preset_name,
            "source": "builtin",
        }
        for preset_name in sorted(name for name in builtin_zenoh_routing_preset_names() if name.startswith("routing/"))
    ]
    return {
        "stress": stress_rows,
        "routing": routing_rows,
    }


class RuntimeEnvironmentListUploadAPIView(APIView):
    """List runtime environments or upload one runtime environment YAML file."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = RuntimeEnvironment.objects.filter(created_by=request.user).order_by("name")
        return Response(RuntimeEnvironmentSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = RuntimeEnvironmentUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        uploaded_file = serializer.validated_data["file"]
        uploaded_rrt_config_file = serializer.validated_data.get("rrt_config_file")

        raw_bytes = uploaded_file.read()
        raw_yaml = raw_bytes.decode("utf-8")

        try:
            payload = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError as exc:
            raise ValidationError({"file": "Invalid YAML content."}) from exc

        (
            name,
            kind,
            metadata,
            images,
            tags,
            parameters,
            command_presets,
            command_groups,
        ) = _extract_runtime_payload(payload)

        new_values = {
            "kind": kind,
            "metadata": metadata,
            "images": images,
            "tags": tags,
            "parameters": parameters,
            "command_preset": command_presets,
            "command_groups": command_groups,
            "raw_payload": payload,
            "raw_yaml": raw_yaml,
            "modified_by": user,
        }

        rrt_config_bytes: bytes | None = None
        rrt_config_name = "serializer_rrt_config.yaml"
        if uploaded_rrt_config_file is not None:
            rrt_config_bytes = uploaded_rrt_config_file.read()
            rrt_config_name = str(uploaded_rrt_config_file.name or rrt_config_name)
        else:
            resolved_rrt = _load_rrt_config_file_from_payload_ref(payload)
            if resolved_rrt[0] is not None:
                rrt_config_bytes, rrt_config_name = resolved_rrt

        existing_env = RuntimeEnvironment.objects.filter(created_by=user, name=name).first()
        if existing_env:
            changed_fields = _collect_field_updates(
                existing_env,
                {k: v for k, v in new_values.items() if k != "raw_yaml"},
            )
            for field_name, value in new_values.items():
                setattr(existing_env, field_name, value)
            env_obj = existing_env
            env_obj.uploaded_version = int(existing_env.uploaded_version or 0) + 1
            env_obj.save()
            created_envs = []
            updated_envs = [{"name": name, "updated_fields": changed_fields}] if changed_fields else []
        else:
            env_obj = RuntimeEnvironment.objects.create(
                created_by=user,
                name=name,
                uploaded_version=1,
                **new_values,
            )
            created_envs = [{"name": env_obj.name, "kind": env_obj.kind}]
            updated_envs = []

        env_obj.cached_yaml_file.save("runtime_env.yaml", ContentFile(raw_bytes), save=False)
        if rrt_config_bytes is not None:
            env_obj.serializer_rrt_config_file.save(
                "serializer_rrt_config.yaml",
                ContentFile(rrt_config_bytes),
                save=False,
            )
            env_obj.serializer_rrt_config_raw_yaml = rrt_config_bytes.decode("utf-8")
            env_obj.serializer_rrt_config_path = str(env_obj.serializer_rrt_config_file.name or "")
        else:
            env_obj.serializer_rrt_config_file = None
            env_obj.serializer_rrt_config_raw_yaml = ""
            env_obj.serializer_rrt_config_path = ""
        env_obj.save(
            update_fields=[
                "cached_yaml_file",
                "serializer_rrt_config_file",
                "serializer_rrt_config_raw_yaml",
                "serializer_rrt_config_path",
                "updated_at",
            ]
        )

        return Response(
            {
                "message": "Runtime environment uploaded successfully.",
                "created": {"runtime_env": created_envs},
                "updated": {"runtime_env": updated_envs},
                "artifacts": {
                    "uploaded_version": env_obj.uploaded_version,
                    "runtime_yaml_path": str(env_obj.cached_yaml_file.name or ""),
                    "rrt_config_yaml_path": str(env_obj.serializer_rrt_config_file.name or ""),
                    "rrt_config_source_file": rrt_config_name if rrt_config_bytes is not None else "",
                },
                "total": {"runtime_env": RuntimeEnvironment.objects.filter(created_by=user).count()},
            },
            status=status.HTTP_200_OK,
        )


class RuntimeActionCatalogAPIView(APIView):
    """Return canonical action catalog with runtime and built-in presets."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        env_name = str(request.query_params.get("env_name") or "").strip()
        runtime_env = None
        if env_name:
            runtime_env = get_object_or_404(RuntimeEnvironment, created_by=request.user, name=env_name)
        return Response(
            {
                "canonical_actions": action_catalog_entries(),
                "runtime_env": runtime_env.name if runtime_env is not None else "",
                "runtime_presets": _runtime_presets_response(runtime_env),
                "builtin_presets": _builtin_presets_response(),
            },
            status=status.HTTP_200_OK,
        )


class RuntimeEnvironmentDeleteAPIView(generics.DestroyAPIView):
    """Delete one runtime environment by id."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return get_object_or_404(RuntimeEnvironment, id=self.kwargs["pk"], created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        env = self.get_object()
        env.delete()
        return Response({"message": f"Deleted runtime environment '{env.name}'."}, status=status.HTTP_200_OK)


class RuntimeEnvironmentDetailAPIView(APIView):
    """Get or delete one runtime environment by name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_env(self, request, env_name: str) -> RuntimeEnvironment:
        return get_object_or_404(RuntimeEnvironment, created_by=request.user, name=env_name)

    def get(self, request, env_name: str, *args, **kwargs):
        env = self.get_env(request, env_name)
        return Response(RuntimeEnvironmentSerializer(env).data, status=status.HTTP_200_OK)

    def delete(self, request, env_name: str, *args, **kwargs):
        env = self.get_env(request, env_name)
        env.delete()
        return Response({"message": f"Deleted runtime environment '{env_name}'."}, status=status.HTTP_200_OK)


class RuntimeEnvironmentYAMLAPIView(APIView):
    """Get one runtime environment YAML by name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, env_name: str, *args, **kwargs):
        env = get_object_or_404(RuntimeEnvironment, created_by=request.user, name=env_name)
        if env.raw_yaml:
            return Response(env.raw_yaml, content_type="application/x-yaml", status=status.HTTP_200_OK)

        rendered = yaml.safe_dump(env.raw_payload, sort_keys=False)
        return Response(rendered, content_type="application/x-yaml", status=status.HTTP_200_OK)

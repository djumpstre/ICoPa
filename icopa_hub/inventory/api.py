"""APIs for inventory management and VM SSH connectivity checks."""

from __future__ import annotations

import logging
import re
from pathlib import Path
import sys

import yaml
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import SSHCredential, VM
from .serializers import (
    InventoryUploadSerializer,
    VMStopContainerSerializer,
    VMConnectivityCheckSerializer,
    VMSerializer,
)
from .celery_tasks import stop_and_remove_vm_container_task


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.append(str(WORKSPACE_ROOT))

from icopa_core.connectors.vm_ssh_client import VMSSHClient

logger = logging.getLogger(__name__)


def _log_check_vm_bad_request(*, username: str, reason: str, vm_name: str = "", payload: dict | None = None) -> None:
    message = (
        "[inventory/check_vm][bad_request] "
        f"user='{username}' vm='{vm_name}' reason='{reason}' payload={payload or {}}"
    )
    print(message)
    logger.warning(message)


def _payload_for_log(raw_payload) -> dict:
    if isinstance(raw_payload, dict):
        return raw_payload
    if hasattr(raw_payload, "dict"):
        try:
            return raw_payload.dict()
        except Exception:
            return {}
    return {}


def _collect_field_updates(obj, new_values: dict) -> dict:
    """Return field-level before/after changes for an existing model instance."""
    changed_fields = {}
    for field_name, new_value in new_values.items():
        old_value = getattr(obj, field_name)
        if old_value != new_value:
            changed_fields[field_name] = {"old": old_value, "new": new_value}
    return changed_fields


def _extract_inventory_entries(payload: dict) -> tuple[list, list]:
    """Return hosts and ssh key entries from either legacy or spec-wrapped YAML."""
    if not isinstance(payload, dict):
        raise ValidationError({"file": "Inventory YAML root must be an object."})
    spec = payload.get("spec")
    if "spec" in payload and spec is not None and not isinstance(spec, dict):
        raise ValidationError({"spec": "spec must be an object when provided."})
    body = spec if isinstance(spec, dict) else payload
    hosts = body.get("hosts") or []
    key_defs = body.get("ssh_key") or []
    if not isinstance(hosts, list):
        raise ValidationError({"hosts": "hosts must be a list."})
    if not isinstance(key_defs, list):
        raise ValidationError({"ssh_key": "ssh_key must be a list."})
    return hosts, key_defs


def _extract_group_name(payload: dict) -> str:
    """Resolve inventory group name from payload metadata, defaulting to 'default'."""
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        group_name = str(metadata.get("group_name") or "").strip()
        if group_name:
            return group_name
    return "default"


def _parse_first_int(stdout: str) -> int | None:
    if isinstance(stdout, bytes):
        raw_text = stdout.decode("utf-8", errors="ignore")
    else:
        raw_text = str(stdout or "")
    line = raw_text.strip().splitlines()
    if not line:
        return None
    first = line[0].strip()
    if not re.match(r"^[0-9]+$", first):
        return None
    return int(first)


def _collect_vm_system_info(ssh: VMSSHClient) -> dict:
    system_info: dict[str, object] = {
        "cpu_cores": None,
        "mem_total_kb": None,
        "mem_total_gb": None,
        "gpu": [],
        "collected_at": timezone.now().isoformat(),
    }

    cpu_result = ssh.execute_command("nproc", timeout=15)
    cpu_cores = _parse_first_int(cpu_result.stdout) if cpu_result.success else None
    if cpu_cores is not None:
        system_info["cpu_cores"] = cpu_cores

    mem_result = ssh.execute_command("awk '/MemTotal:/ {print $2}' /proc/meminfo", timeout=15)
    mem_total_kb = _parse_first_int(mem_result.stdout) if mem_result.success else None
    if mem_total_kb is not None:
        system_info["mem_total_kb"] = mem_total_kb
        system_info["mem_total_gb"] = round(mem_total_kb / (1024 * 1024), 3)

    gpu_result = ssh.execute_command(
        "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || true",
        timeout=15,
    )
    gpu_lines = [line.strip() for line in (gpu_result.stdout or "").splitlines() if line.strip()]
    if not gpu_lines:
        lspci_result = ssh.execute_command("lspci | grep -Ei 'vga|3d|nvidia|amd' || true", timeout=15)
        gpu_lines = [line.strip() for line in (lspci_result.stdout or "").splitlines() if line.strip()]
    system_info["gpu"] = gpu_lines

    return system_info


def _collect_managed_container_status(
    ssh: VMSSHClient,
    managed_containers: list[str],
    running_containers: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    statuses: list[dict[str, str]] = []
    running_names: list[str] = []
    running_by_name: dict[str, dict[str, str]] = {}
    for item in running_containers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        running_by_name[name] = item
    for raw_name in managed_containers:
        name = str(raw_name).strip()
        if not name:
            continue
        running_info = running_by_name.get(name)
        if isinstance(running_info, dict):
            statuses.append(
                {
                    "name": name,
                    "state": "running",
                    "status": str(running_info.get("status") or ""),
                    "id": str(running_info.get("id") or ""),
                    "image": str(running_info.get("image") or ""),
                    "command": str(running_info.get("command") or ""),
                    "created_at": str(running_info.get("created_at") or ""),
                    "running_for": str(running_info.get("running_for") or ""),
                    "ports": str(running_info.get("ports") or ""),
                }
            )
            running_names.append(name)
            continue

        all_result = ssh.execute_command(
            f"docker ps -a --filter \"name=^/{name}$\" --format '{{{{.Names}}}}|{{{{.Status}}}}'",
            timeout=15,
        )
        if all_result.success and (all_result.stdout or "").strip():
            status_text = (all_result.stdout or "").strip().splitlines()[0]
            statuses.append(
                {
                    "name": name,
                    "state": "stopped",
                    "status": status_text,
                    "id": "",
                    "image": "",
                    "command": "",
                    "created_at": "",
                    "running_for": "",
                    "ports": "",
                }
            )
            continue

        statuses.append(
            {
                "name": name,
                "state": "removed",
                "status": "",
                "id": "",
                "image": "",
                "command": "",
                "created_at": "",
                "running_for": "",
                "ports": "",
            }
        )
    return statuses, running_names


def _filter_running_icopa_containers(containers: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in containers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if "icopa" not in name.lower():
            continue
        output.append(item)
    return output


class InventoryListUploadAPIView(APIView):
    """List VMs or upload an inventory YAML file."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        queryset = VM.objects.filter(created_by=request.user).select_related("credential").order_by("name")
        return Response(VMSerializer(queryset, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = InventoryUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        uploaded_file = serializer.validated_data["file"]

        try:
            payload = yaml.safe_load(uploaded_file.read()) or {}
        except yaml.YAMLError as exc:
            raise ValidationError({"file": "Invalid YAML content."}) from exc

        hosts, key_defs = _extract_inventory_entries(payload)
        group_name = _extract_group_name(payload)

        created_hosts = []
        updated_hosts = []
        created_keys = []
        updated_keys = []
        credentials_by_name: dict[str, SSHCredential] = {}

        for key_item in key_defs:
            if not isinstance(key_item, dict):
                raise ValidationError({"ssh_key": "Each ssh_key item must be an object."})
            key_name = key_item.get("name")
            key_path = key_item.get("key_path")
            if not key_name or not key_path:
                raise ValidationError({"ssh_key": "Each ssh_key item requires name and key_path."})
            key_upload = request.FILES.get(f"ssh_key_file__{key_name}")

            existing_cred = SSHCredential.objects.filter(created_by=user, name=key_name).first()
            new_values = {
                "key_path": key_path,
                "user_name": (
                    key_item.get("user_name")
                    or key_item.get("username")
                    or (existing_cred.user_name if existing_cred else "root")
                ),
                "description": key_item.get("description") or "",
            }

            if existing_cred:
                changed_fields = _collect_field_updates(existing_cred, new_values)
                cred_obj = existing_cred
                for field_name, value in new_values.items():
                    setattr(cred_obj, field_name, value)
                if key_upload:
                    old_name = cred_obj.key_file.name or ""
                    cred_obj.key_file.save(key_upload.name, key_upload, save=False)
                    if old_name != cred_obj.key_file.name:
                        changed_fields["key_file"] = {"old": old_name, "new": cred_obj.key_file.name}
                cred_obj.save()
                if changed_fields:
                    updated_keys.append({"name": key_name, "updated_fields": changed_fields})
            else:
                cred_obj = SSHCredential(created_by=user, name=key_name, **new_values)
                if key_upload:
                    cred_obj.key_file.save(key_upload.name, key_upload, save=False)
                cred_obj.save()
                created_keys.append({
                    "name": key_name,
                    "key_path": cred_obj.key_path,
                    "key_file": cred_obj.key_file.name if cred_obj.key_file else "",
                    "user_name": cred_obj.user_name,
                })

            credentials_by_name[key_name] = cred_obj

        for host in hosts:
            if not isinstance(host, dict):
                raise ValidationError({"hosts": "Each hosts item must be an object."})

            vm_name = host.get("name")
            address = host.get("address")
            if not vm_name or not address:
                raise ValidationError({"hosts": "Each host requires name and address."})

            credential_ref = (host.get("credential") or {}).get("ssh_key_name")
            cred_obj = credentials_by_name.get(credential_ref)
            if credential_ref and cred_obj is None:
                cred_obj = SSHCredential.objects.filter(created_by=user, name=credential_ref).first()
                if cred_obj:
                    credentials_by_name[credential_ref] = cred_obj
            if credential_ref and cred_obj is None:
                raise ValidationError({"hosts": f"Unknown ssh_key_name '{credential_ref}' for host '{vm_name}'."})
            networking = host.get("networking") or {}
            if not isinstance(networking, dict):
                raise ValidationError(
                    {"hosts": f"'networking' must be an object for host '{vm_name}'."}
                )

            existing_vm = VM.objects.filter(created_by=user, name=vm_name).first()
            vm_user_name = (
                host.get("user_name")
                or host.get("username")
                or (existing_vm.user_name if existing_vm else "")
                or (cred_obj.user_name if cred_obj else "")
                or "root"
            )

            new_values = {
                "group_name": group_name,
                "address": address,
                "user_name": vm_user_name,
                "port": int(host.get("port") or 22),
                "description": host.get("description") or "",
                "metadata": host.get("metadata") or {},
                "networking": networking,
                "credential": cred_obj,
                "modified_by": user,
            }

            if existing_vm:
                changed_fields = _collect_field_updates(existing_vm, new_values)
                VM.objects.update_or_create(
                    created_by=user,
                    name=vm_name,
                    defaults=new_values,
                )
                if changed_fields:
                    updated_hosts.append({"name": vm_name, "updated_fields": changed_fields})
            else:
                vm_obj = VM.objects.create(created_by=user, name=vm_name, **new_values)
                created_hosts.append({
                    "name": vm_obj.name,
                    "group_name": vm_obj.group_name,
                    "address": vm_obj.address,
                    "user_name": vm_obj.user_name,
                    "port": vm_obj.port,
                    "credential": credential_ref,
                    "networking": vm_obj.networking,
                })

        return Response(
            {
                "message": "Inventory uploaded successfully.",
                "created": {
                    "hosts": created_hosts,
                    "ssh_key": created_keys,
                },
                "updated": {
                    "hosts": updated_hosts,
                    "ssh_key": updated_keys,
                },
                "total": {
                    "hosts": VM.objects.filter(created_by=user).count(),
                    "ssh_key": SSHCredential.objects.filter(created_by=user).count(),
                },
            },
            status=status.HTTP_200_OK,
        )


class InventoryDeleteAPIView(generics.DestroyAPIView):
    """Delete one VM inventory entry."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return get_object_or_404(VM, id=self.kwargs["pk"], created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        vm = self.get_object()
        vm.delete()
        return Response({"message": f"Deleted VM '{vm.name}'."}, status=status.HTTP_200_OK)


class InventoryVMDetailAPIView(APIView):
    """Get or delete one VM by VM name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get_vm(self, request, vm_name: str) -> VM:
        return get_object_or_404(
            VM.objects.select_related("credential"),
            created_by=request.user,
            name=vm_name,
        )

    def get(self, request, vm_name: str, *args, **kwargs):
        vm = self.get_vm(request, vm_name)
        return Response(VMSerializer(vm).data, status=status.HTTP_200_OK)

    def delete(self, request, vm_name: str, *args, **kwargs):
        vm = self.get_vm(request, vm_name)
        vm.delete()
        return Response({"message": f"Deleted VM '{vm_name}'."}, status=status.HTTP_200_OK)


class InventoryCheckVMAPIView(APIView):
    """Validate SSH connectivity for one VM by name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = VMConnectivityCheckSerializer(data=request.data, context={"request": request})
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError:
            _log_check_vm_bad_request(
                username=str(getattr(request.user, "username", "")),
                reason="request validation failed",
                payload=_payload_for_log(request.data),
            )
            raise
        user = serializer.validated_data["user"]

        vm_name = serializer.validated_data["name"]
        vm = VM.objects.select_related("credential").filter(created_by=user, name=vm_name).first()
        if vm is None:
            _log_check_vm_bad_request(
                username=str(user.username),
                reason="vm not found for user",
                vm_name=str(vm_name),
                payload=_payload_for_log(request.data),
            )
            raise ValidationError({"name": f"VM '{vm_name}' not found for current user."})

        if vm.credential is None:
            _log_check_vm_bad_request(
                username=str(user.username),
                reason="vm has no ssh credential",
                vm_name=str(vm_name),
                payload=_payload_for_log(request.data),
            )
            raise ValidationError({"name": f"VM '{vm_name}' has no SSH credential configured."})
        key_path = vm.credential.key_file.path if vm.credential.key_file else vm.credential.key_path
        if not key_path:
            _log_check_vm_bad_request(
                username=str(user.username),
                reason="vm credential has empty key path",
                vm_name=str(vm_name),
                payload=_payload_for_log(request.data),
            )
            raise ValidationError({"name": f"VM '{vm_name}' has no SSH key path configured."})

        ssh = VMSSHClient(
            host=vm.address,
            username=vm.user_name,
            port=vm.port,
            key_path=key_path,
        )
        ssh_result = ssh.test_connectivity(check_container_runtime=False)
        docker_result = None
        docker_ok = False
        docker_stdout = ""
        docker_stderr = ""
        docker_returncode = 0
        if ssh_result.success:
            docker_result = ssh.check_docker_runtime()
            docker_stdout = str(getattr(docker_result, "stdout", "") or "").strip()
            docker_stderr = str(getattr(docker_result, "stderr", "") or "").strip()
            docker_returncode = int(getattr(docker_result, "returncode", 0) or 0)
            docker_ok = bool(getattr(docker_result, "success", False)) and docker_stdout == "docker_ok"

        overall_ok = bool(ssh_result.success and docker_ok)
        system_info = _collect_vm_system_info(ssh) if ssh_result.success else {}
        tracked_containers = [str(item) for item in (vm.managed_containers or []) if str(item).strip()]
        managed_container_status: list[dict[str, str]] = []
        started_containers: list[str] = []
        discovered_running_containers: list[str] = []
        running_containers: list[dict[str, str]] = []
        if ssh_result.success and docker_ok:
            running_container_items = ssh.list_running_containers(timeout=15)
            running_containers = running_container_items if isinstance(running_container_items, list) else []
            discovered_running_containers = sorted(
                {
                    str(item.get("name") or "").strip()
                    for item in _filter_running_icopa_containers(running_containers)
                    if str(item.get("name") or "").strip()
                }
            )
            status_targets = sorted({*tracked_containers, *discovered_running_containers})
            if status_targets:
                managed_container_status, started_containers = _collect_managed_container_status(
                    ssh,
                    status_targets,
                    running_containers,
                )

        vm.last_connection_time = timezone.now()
        vm.status = VM.VMStatus.ACTIVE if ssh_result.success else VM.VMStatus.ERROR
        vm.container_runtime_type = "docker" if ssh_result.success else ""
        vm.container_runtime_ready = bool(ssh_result.success and docker_ok)
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        running_managed_containers: list[str] = []
        if isinstance(system_info, dict) and system_info:
            vm.system_info = system_info
            metadata["capabilities"] = system_info
        if ssh_result.success and docker_ok:
            running_managed_containers = sorted(
                {
                    name
                    for name in [*started_containers, *discovered_running_containers]
                    if str(name).strip()
                }
            )
            vm.managed_containers = running_managed_containers
        connectivity_info = {
            "ssh_ok": bool(ssh_result.success),
            "ssh_returncode": int(getattr(ssh_result, "returncode", 0) or 0),
            "ssh_stdout": str(getattr(ssh_result, "stdout", "") or ""),
            "ssh_stderr": str(getattr(ssh_result, "stderr", "") or ""),
            "docker_ok": bool(docker_ok),
            "docker_returncode": docker_returncode,
            "docker_stdout": docker_stdout,
            "docker_stderr": docker_stderr,
            "checked_at": timezone.now().isoformat(),
        }
        metadata["connectivity"] = connectivity_info
        metadata["managed_container_status"] = managed_container_status
        metadata["managed_containers_running"] = (
            running_managed_containers if (ssh_result.success and docker_ok) else started_containers
        )
        vm.metadata = metadata
        vm.modified_by = user
        update_fields = [
            "last_connection_time",
            "status",
            "container_runtime_type",
            "container_runtime_ready",
            "metadata",
            "modified_by",
            "updated_at",
        ]
        if system_info:
            update_fields.append("system_info")
        if ssh_result.success and docker_ok:
            update_fields.append("managed_containers")
        vm.save(update_fields=update_fields)

        stderr_text = str(getattr(ssh_result, "stderr", "") or "")
        returncode = int(getattr(ssh_result, "returncode", 0) or 0)
        stdout_text = str(getattr(ssh_result, "stdout", "") or "")
        if ssh_result.success and not docker_ok:
            stderr_text = docker_stderr or f"Docker runtime check failed: {docker_stdout or 'docker_error'}"
            returncode = docker_returncode
            stdout_text = docker_stdout or stdout_text

        return Response(
            {
                "name": vm.name,
                "address": vm.address,
                "port": vm.port,
                "ok": overall_ok,
                "ssh_ok": bool(ssh_result.success),
                "docker_ok": bool(docker_ok),
                "returncode": returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "container_runtime_type": vm.container_runtime_type,
                "container_runtime_ready": vm.container_runtime_ready,
                "system_info": system_info,
                "managed_containers": running_managed_containers if (ssh_result.success and docker_ok) else tracked_containers,
                "started_containers": started_containers,
                "managed_container_status": managed_container_status,
            },
            status=status.HTTP_200_OK,
        )


class InventoryStopContainerAPIView(APIView):
    """Stop and remove one container on a VM by vm id or vm name."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    _ACTIVE_TASK_STATES = {"PENDING", "STARTED", "RETRY", "PROGRESS"}

    @staticmethod
    def _running_response(*, vm: VM, container_name: str, task_id: str, message: str) -> dict:
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        return {
            "ok": False,
            "status": "RUNNING",
            "state": "running",
            "message": message,
            "task_id": task_id,
            "vm_id": vm.id,
            "vm": vm.name,
            "container": container_name,
            "managed_containers": vm.managed_containers if isinstance(vm.managed_containers, list) else [],
            "managed_container_status": metadata.get("managed_container_status", []),
        }

    @staticmethod
    def _resolve_vm_for_request(*, user, vm_id: int | None, vm_name: str | None) -> VM:
        queryset = VM.objects.select_related("credential").filter(created_by=user)
        if vm_id is not None:
            return get_object_or_404(queryset, id=vm_id)
        return get_object_or_404(queryset, name=str(vm_name or "").strip())

    @staticmethod
    def _read_container_op(vm: VM, container_name: str) -> dict:
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        container_ops = metadata.get("container_ops")
        if not isinstance(container_ops, dict):
            return {}
        op = container_ops.get(container_name)
        return op if isinstance(op, dict) else {}

    @staticmethod
    def _save_container_op(*, vm: VM, user, container_name: str, task_id: str) -> None:
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        container_ops = metadata.get("container_ops")
        if not isinstance(container_ops, dict):
            container_ops = {}
        container_ops[container_name] = {
            "task_id": task_id,
            "status": "RUNNING",
            "state": "running",
            "message": f"Stop/remove is running for container '{container_name}'.",
            "updated_at": timezone.now().isoformat(),
        }
        metadata["container_ops"] = container_ops
        vm.metadata = metadata
        vm.modified_by = user
        vm.save(update_fields=["metadata", "modified_by", "updated_at"])

    def post(self, request, *args, **kwargs):
        serializer = VMStopContainerSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        vm = self._resolve_vm_for_request(
            user=user,
            vm_id=serializer.validated_data.get("vm_id"),
            vm_name=serializer.validated_data.get("vm"),
        )
        container_name = str(serializer.validated_data["container"]).strip()
        task_id = str(serializer.validated_data.get("task_id") or "").strip()

        if task_id:
            op = self._read_container_op(vm, container_name)
            if op.get("task_id") == task_id and str(op.get("status") or "").upper() in {"SUCCEEDED", "FAILED"}:
                metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
                response = {
                    "ok": str(op.get("status") or "").upper() == "SUCCEEDED",
                    "status": str(op.get("status") or "").upper(),
                    "state": str(op.get("state") or ""),
                    "message": str(op.get("message") or ""),
                    "error": str(op.get("error") or ""),
                    "task_id": task_id,
                    "vm_id": vm.id,
                    "vm": vm.name,
                    "container": container_name,
                    "managed_containers": vm.managed_containers if isinstance(vm.managed_containers, list) else [],
                    "managed_container_status": metadata.get("managed_container_status", []),
                }
                return Response(response, status=status.HTTP_200_OK)

            async_result = stop_and_remove_vm_container_task.AsyncResult(task_id)
            async_state = str(async_result.state or "PENDING").upper()
            if async_state in self._ACTIVE_TASK_STATES:
                return Response(
                    self._running_response(
                        vm=vm,
                        container_name=container_name,
                        task_id=task_id,
                        message=f"Stop/remove is running for container '{container_name}'.",
                    ),
                    status=status.HTTP_200_OK,
                )

            vm.refresh_from_db()
            op = self._read_container_op(vm, container_name)
            if op.get("task_id") == task_id and str(op.get("status") or "").upper() in {"SUCCEEDED", "FAILED"}:
                metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
                response = {
                    "ok": str(op.get("status") or "").upper() == "SUCCEEDED",
                    "status": str(op.get("status") or "").upper(),
                    "state": str(op.get("state") or ""),
                    "message": str(op.get("message") or ""),
                    "error": str(op.get("error") or ""),
                    "task_id": task_id,
                    "vm_id": vm.id,
                    "vm": vm.name,
                    "container": container_name,
                    "managed_containers": vm.managed_containers if isinstance(vm.managed_containers, list) else [],
                    "managed_container_status": metadata.get("managed_container_status", []),
                }
                return Response(response, status=status.HTTP_200_OK)

            payload = async_result.result if isinstance(async_result.result, dict) else {}
            if payload:
                return Response(payload, status=status.HTTP_200_OK)

            return Response(
                {
                    "ok": False,
                    "status": "FAILED",
                    "state": "task_finished_unknown",
                    "message": "Task finished but no result payload is available.",
                    "task_id": task_id,
                    "vm_id": vm.id,
                    "vm": vm.name,
                    "container": container_name,
                },
                status=status.HTTP_200_OK,
            )

        queue_failed = None
        task = None
        try:
            task = stop_and_remove_vm_container_task.apply_async(
                kwargs={
                    "user_id": user.id,
                    "vm_id": vm.id,
                    "container_name": container_name,
                },
                retry=False,
            )
        except Exception as exc:  # fallback when broker is unavailable
            queue_failed = str(exc)

        if queue_failed is not None:
            result = stop_and_remove_vm_container_task.run(
                user_id=user.id,
                vm_id=vm.id,
                container_name=container_name,
            )
            return Response(result, status=status.HTTP_200_OK)

        task_id = str(task.id or "")
        self._save_container_op(vm=vm, user=user, container_name=container_name, task_id=task_id)
        return Response(
            self._running_response(
                vm=vm,
                container_name=container_name,
                task_id=task_id,
                message=f"Stop/remove queued for container '{container_name}'.",
            ),
            status=status.HTTP_202_ACCEPTED,
        )

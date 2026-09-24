"""Celery tasks for inventory operations."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from celery import shared_task
from django.utils import timezone

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.append(str(WORKSPACE_ROOT))

from icopa_core.connectors.vm_ssh_client import VMSSHClient

from .models import VM


def _upsert_container_op(
    metadata: dict[str, Any],
    *,
    container_name: str,
    task_id: str,
    status: str,
    state: str,
    message: str,
    error: str = "",
) -> None:
    container_ops = metadata.get("container_ops")
    if not isinstance(container_ops, dict):
        container_ops = {}
    container_ops[str(container_name)] = {
        "task_id": str(task_id or ""),
        "status": str(status),
        "state": str(state),
        "message": str(message or ""),
        "error": str(error or ""),
        "updated_at": timezone.now().isoformat(),
    }
    metadata["container_ops"] = container_ops


def _collect_managed_container_status(
    ssh: VMSSHClient,
    managed_containers: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    statuses: list[dict[str, str]] = []
    running_names: list[str] = []
    for raw_name in managed_containers:
        name = str(raw_name).strip()
        if not name:
            continue
        status = ssh.get_container_status(name)
        if status.running:
            statuses.append(
                {
                    "name": name,
                    "state": "running",
                    "status": status.status,
                }
            )
            running_names.append(name)
            continue
        if status.exists:
            statuses.append(
                {
                    "name": name,
                    "state": "stopped",
                    "status": status.status,
                }
            )
            continue
        statuses.append(
            {
                "name": name,
                "state": "removed",
                "status": "",
            }
        )
    return statuses, running_names


@shared_task(bind=True)
def stop_and_remove_vm_container_task(
    self,
    *,
    user_id: int,
    container_name: str,
    vm_id: int | None = None,
    vm_name: str | None = None,
) -> dict:
    task_id = str(getattr(getattr(self, "request", None), "id", "") or "")
    vm_queryset = VM.objects.select_related("credential").filter(created_by_id=user_id)
    vm = None
    if vm_id is not None:
        vm = vm_queryset.filter(id=vm_id).first()
    elif vm_name:
        vm = vm_queryset.filter(name=str(vm_name).strip()).first()
    if vm is None:
        return {
            "ok": False,
            "status": "FAILED",
            "state": "vm_not_found",
            "message": "Target VM was not found.",
            "task_id": task_id,
            "vm_id": vm_id or "",
            "vm": vm_name or "",
            "container": str(container_name or ""),
        }

    if vm.credential is None:
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        _upsert_container_op(
            metadata,
            container_name=str(container_name or ""),
            task_id=task_id,
            status="FAILED",
            state="vm_invalid",
            message=f"VM '{vm.name}' has no SSH credential configured.",
        )
        vm.metadata = metadata
        vm.modified_by_id = user_id
        vm.save(update_fields=["metadata", "modified_by", "updated_at"])
        return {
            "ok": False,
            "status": "FAILED",
            "state": "vm_invalid",
            "message": f"VM '{vm.name}' has no SSH credential configured.",
            "task_id": task_id,
            "vm_id": vm.id,
            "vm": vm.name,
            "container": str(container_name or ""),
        }
    key_path = vm.credential.key_file.path if vm.credential.key_file else vm.credential.key_path
    if not key_path:
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        _upsert_container_op(
            metadata,
            container_name=str(container_name or ""),
            task_id=task_id,
            status="FAILED",
            state="vm_invalid",
            message=f"VM '{vm.name}' has no SSH key path configured.",
        )
        vm.metadata = metadata
        vm.modified_by_id = user_id
        vm.save(update_fields=["metadata", "modified_by", "updated_at"])
        return {
            "ok": False,
            "status": "FAILED",
            "state": "vm_invalid",
            "message": f"VM '{vm.name}' has no SSH key path configured.",
            "task_id": task_id,
            "vm_id": vm.id,
            "vm": vm.name,
            "container": str(container_name or ""),
        }

    metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
    _upsert_container_op(
        metadata,
        container_name=str(container_name or ""),
        task_id=task_id,
        status="RUNNING",
        state="running",
        message=f"Stop/remove is running for container '{container_name}'.",
    )
    vm.metadata = metadata
    vm.modified_by_id = user_id
    vm.save(update_fields=["metadata", "modified_by", "updated_at"])

    ssh = VMSSHClient(
        host=vm.address,
        username=vm.user_name,
        port=vm.port,
        key_path=key_path,
    )
    connectivity = ssh.test_connectivity()
    if not connectivity.success:
        vm.last_connection_time = timezone.now()
        vm.status = VM.VMStatus.ERROR
        vm.modified_by_id = user_id
        metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
        _upsert_container_op(
            metadata,
            container_name=str(container_name or ""),
            task_id=task_id,
            status="FAILED",
            state="vm_unreachable",
            message=f"VM '{vm.name}' is not reachable.",
            error=connectivity.stderr,
        )
        vm.metadata = metadata
        vm.save(update_fields=["last_connection_time", "status", "metadata", "modified_by", "updated_at"])
        return {
            "ok": False,
            "status": "FAILED",
            "state": "vm_unreachable",
            "message": f"VM '{vm.name}' is not reachable.",
            "task_id": task_id,
            "vm_id": vm.id,
            "vm": vm.name,
            "container": str(container_name or ""),
            "error": connectivity.stderr,
        }

    lifecycle_result = ssh.stop_and_remove_container(container_name)
    tracked_containers = sorted(
        {
            *[str(item) for item in (vm.managed_containers or []) if str(item).strip()],
            str(container_name or "").strip(),
        }
    )
    managed_container_status, started_containers = _collect_managed_container_status(ssh, tracked_containers)

    vm.last_connection_time = timezone.now()
    vm.status = VM.VMStatus.ACTIVE
    vm.modified_by_id = user_id
    vm.managed_containers = sorted({str(item) for item in started_containers if str(item).strip()})
    metadata = vm.metadata if isinstance(vm.metadata, dict) else {}
    metadata["managed_container_status"] = managed_container_status
    metadata["managed_containers_running"] = vm.managed_containers
    _upsert_container_op(
        metadata,
        container_name=str(container_name or ""),
        task_id=task_id,
        status="SUCCEEDED" if lifecycle_result.ok else "FAILED",
        state=lifecycle_result.state,
        message=lifecycle_result.message,
        error=lifecycle_result.error,
    )
    vm.metadata = metadata
    vm.save(update_fields=["last_connection_time", "status", "managed_containers", "metadata", "modified_by", "updated_at"])

    payload = {
        "ok": lifecycle_result.ok,
        "status": "SUCCEEDED" if lifecycle_result.ok else "FAILED",
        "state": lifecycle_result.state,
        "message": lifecycle_result.message,
        "task_id": task_id,
        "vm_id": vm.id,
        "vm": vm.name,
        "container": str(container_name or "").strip(),
        "managed_containers": vm.managed_containers,
        "managed_container_status": managed_container_status,
        "stop_stdout": lifecycle_result.stop_stdout,
        "stop_stderr": lifecycle_result.stop_stderr,
        "remove_stdout": lifecycle_result.remove_stdout,
        "remove_stderr": lifecycle_result.remove_stderr,
    }
    if lifecycle_result.error:
        payload["error"] = lifecycle_result.error
    return payload

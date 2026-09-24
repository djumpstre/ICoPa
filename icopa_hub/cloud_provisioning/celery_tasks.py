"""Celery tasks for cloud VM provisioning and deprovisioning."""

from __future__ import annotations

from dataclasses import asdict
import logging
import os
from pathlib import Path
import sys
import time

from celery import shared_task
from django.db import transaction

from inventory.models import VM

from .models import (
    ProvisioningVM,
    ProvisioningVMCheckRequest,
    ProvisioningVMEvent,
    ProvisioningVMStartRequest,
    ProvisioningVMStopRequest,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.append(str(WORKSPACE_ROOT))

from icopa_core.cloud_provisioning import get_provisioner
from icopa_core.cloud_provisioning.base import DeleteVMRequest, ProvisionVMRequest, StartVMRequest, StopVMRequest
from icopa_core.cloud_provisioning.azure import build_provision_request_from_vm_config

logger = logging.getLogger(__name__)

LOG_VALUE_MAX_CHARS = 180
SENSITIVE_LOG_KEYWORDS = (
    "secret",
    "token",
    "password",
    "private_key",
    "ssh_public_key",
    "credential",
    "raw_template",
    "request_spec",
    "result_data",
    "event_data",
    "check_data",
    "start_data",
    "stop_data",
)


def _resource_name_from_id(resource_id: str) -> str:
    rid = str(resource_id).rstrip("/")
    if not rid:
        return ""
    return rid.split("/")[-1]


def _is_sensitive_log_field(key: str) -> bool:
    key_lc = str(key or "").strip().lower()
    return any(keyword in key_lc for keyword in SENSITIVE_LOG_KEYWORDS)


def _format_log_value(key: str, value: object) -> str:
    """
    Keep celery task logs concise: print only key execution signals, not raw payloads.
    """
    if _is_sensitive_log_field(key):
        return "<redacted>"
    if isinstance(value, dict):
        keys = sorted(str(k) for k in value.keys())
        preview = ",".join(keys[:6])
        if len(keys) > 6:
            preview = f"{preview},..."
        return f"<dict keys={preview or '<none>'} size={len(keys)}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__} size={len(value)}>"
    text = " ".join(str(value).split())
    if len(text) > LOG_VALUE_MAX_CHARS:
        text = f"{text[: LOG_VALUE_MAX_CHARS - 3]}..."
    return text


def _task_log(task_name: str, message: str, **fields) -> None:
    extra = " ".join(f"{key}={_format_log_value(key, value)}" for key, value in fields.items())
    line = f"[{task_name}] {message}" + (f" | {extra}" if extra else "")
    print(line)
    logger.info(line)


def _elapsed_seconds(start: float) -> str:
    return f"{time.monotonic() - start:.2f}s"


def _append_event(
    provisioning_vm: ProvisioningVM,
    level: str,
    message: str,
    event_data: dict | None = None,
) -> None:
    ProvisioningVMEvent.objects.create(
        provisioning_vm=provisioning_vm,
        level=level,
        message=message,
        event_data=event_data or {},
    )


def _resolve_credentials() -> dict:
    required = ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_SECRET")
    creds = {name: str(os.getenv(name, "")).strip() for name in required}
    missing = [name for name, value in creds.items() if not value]
    if missing:
        raise ValueError(f"Missing required Azure credentials in environment: {', '.join(missing)}")
    return creds


def _build_provision_request(provisioning_vm: ProvisioningVM, creds: dict) -> ProvisionVMRequest:
    spec = provisioning_vm.request_spec or {}
    subscription_selector_default = str(
        spec.get("subscription_selector")
        or os.getenv("AZURE_SUBSCRIPTION_ID", "")
        or os.getenv("AZURE_SUBSCRIPTION_SELECTOR", "")
    ).strip()
    if not subscription_selector_default:
        raise ValueError("subscription_selector is required in request_spec or Azure subscription environment.")

    raw_vm_cfg: dict = {}
    raw_template = provisioning_vm.raw_template if isinstance(provisioning_vm.raw_template, dict) else {}
    raw_vms = raw_template.get("vms")
    if isinstance(raw_vms, list) and raw_vms:
        wanted_name = str(spec.get("vm_name") or provisioning_vm.name).strip()
        for item in raw_vms:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip() == wanted_name:
                raw_vm_cfg = item
                break
        if not raw_vm_cfg and len(raw_vms) == 1 and isinstance(raw_vms[0], dict):
            raw_vm_cfg = raw_vms[0]

    # Prefer normalized fields already validated and stored in request_spec.
    merged_vm_cfg = dict(raw_vm_cfg)
    merged_vm_cfg.update(
        {
            "subscription_selector": subscription_selector_default,
            "subscription_id": subscription_selector_default,
            "resource_group": str(spec.get("resource_group", "")),
            "location": str(spec.get("location", "")),
            "name": str(spec.get("vm_name") or provisioning_vm.name),
            "vm_name": str(spec.get("vm_name") or provisioning_vm.name),
            "size": str(spec.get("vm_size", "")),
            "vm_size": str(spec.get("vm_size", "")),
            "admin_username": str(spec.get("admin_username", "ubuntu")),
            "ssh_public_key": str(spec.get("ssh_public_key", "")),
            "image": spec.get("image") or {},
            "network": spec.get("network") or {},
            "spot": spec.get("spot") or {},
            "storage": spec.get("storage") or {},
            "tags": spec.get("tags") or {},
        }
    )
    custom_data = str(spec.get("custom_data", "")).strip()
    if custom_data:
        merged_vm_cfg["custom_data"] = custom_data

    return build_provision_request_from_vm_config(
        merged_vm_cfg,
        default_subscription_selector=subscription_selector_default,
        default_vm_name=str(spec.get("vm_name") or provisioning_vm.name),
    )


def _upsert_inventory_vm(
    provisioning_vm: ProvisioningVM,
    *,
    vm_name: str,
    address: str,
    result_data: dict,
) -> VM:
    group_name = (
        str(provisioning_vm.request_spec.get("group_name") or "").strip()
        or str(provisioning_vm.group_name or "").strip()
        or "default"
    )
    private_key_name = str(provisioning_vm.request_spec.get("ssh_private_key_name_in_icopa") or "").strip()
    if not private_key_name and provisioning_vm.ssh_credential is not None:
        private_key_name = str(provisioning_vm.ssh_credential.name or "").strip()
    icopa_config = provisioning_vm.icopa_config if isinstance(provisioning_vm.icopa_config, dict) else {}
    if not icopa_config:
        spec_icopa = provisioning_vm.request_spec.get("icopa_config")
        if isinstance(spec_icopa, dict):
            icopa_config = spec_icopa
    icopa_networking = icopa_config.get("networking") if isinstance(icopa_config.get("networking"), dict) else {}
    defaults = {
        "group_name": group_name,
        "address": address,
        "user_name": str(provisioning_vm.request_spec.get("admin_username", "ubuntu")),
        "port": 22,
        "status": VM.VMStatus.ACTIVE,
        "archived": False,
        "auto_provisioned": True,
        "cloud_provisioning_vm": provisioning_vm,
        "networking": icopa_networking,
        "metadata": {
            "cloud_managed": True,
            "provider": provisioning_vm.provider.lower(),
            "provisioning_vm_id": provisioning_vm.id,
            "resource_ids": result_data.get("resource_ids", {}),
            "ssh_private_key_name_in_icopa": private_key_name,
            "ssh_credential_id": provisioning_vm.ssh_credential_id,
            "icopa_config": icopa_config,
        },
        "credential": provisioning_vm.ssh_credential,
        "modified_by": provisioning_vm.created_by,
    }
    vm, _ = VM.objects.update_or_create(
        created_by=provisioning_vm.created_by,
        name=vm_name,
        defaults=defaults,
    )
    return vm


@shared_task(bind=True)
def provision_vm_task(self, *, provisioning_vm_id: int, user_id: int) -> dict:
    task_started = time.monotonic()
    _task_log("provision_vm_task", "received task", provisioning_vm_id=provisioning_vm_id, user_id=user_id)
    with transaction.atomic():
        provisioning_vm = ProvisioningVM.objects.select_for_update().select_related(
            "ssh_credential",
            "created_by",
        ).get(id=provisioning_vm_id, created_by_id=user_id)
        provisioning_vm.status = ProvisioningVM.Status.RUNNING
        provisioning_vm.modified_by_id = user_id
        provisioning_vm.task_id = self.request.id or provisioning_vm.task_id
        provisioning_vm.save(update_fields=["status", "modified_by", "task_id", "updated_at"])
    _task_log(
        "provision_vm_task",
        "status set to RUNNING",
        provisioning_vm_id=provisioning_vm_id,
        task_id=self.request.id or "",
        vm_name=provisioning_vm.name,
        elapsed=_elapsed_seconds(task_started),
    )
    _append_event(provisioning_vm, "INFO", "Provisioning started.")

    try:
        step_started = time.monotonic()
        _task_log("provision_vm_task", "resolving Azure credentials", provisioning_vm_id=provisioning_vm_id)
        creds = _resolve_credentials()
        _task_log(
            "provision_vm_task",
            "credentials resolved",
            provisioning_vm_id=provisioning_vm_id,
            step_elapsed=_elapsed_seconds(step_started),
            elapsed=_elapsed_seconds(task_started),
        )
        step_started = time.monotonic()
        _task_log(
            "provision_vm_task",
            "initializing provisioner",
            provisioning_vm_id=provisioning_vm_id,
            provider=provisioning_vm.provider,
        )
        provisioner = get_provisioner(provisioning_vm.provider, creds)
        _task_log(
            "provision_vm_task",
            "provisioner initialized",
            provisioning_vm_id=provisioning_vm_id,
            step_elapsed=_elapsed_seconds(step_started),
            elapsed=_elapsed_seconds(task_started),
        )
        step_started = time.monotonic()
        _task_log("provision_vm_task", "building provision request", provisioning_vm_id=provisioning_vm_id)
        req = _build_provision_request(provisioning_vm, creds)
        _task_log(
            "provision_vm_task",
            "request ready",
            provisioning_vm_id=provisioning_vm_id,
            subscription_selector=req.subscription_selector,
            resource_group=req.resource_group,
            vm_name=req.vm_name,
            vm_size=req.vm_size,
            location=req.location,
            step_elapsed=_elapsed_seconds(step_started),
            elapsed=_elapsed_seconds(task_started),
        )
        step_started = time.monotonic()
        _task_log("provision_vm_task", "calling provisioner.provision_vm", provisioning_vm_id=provisioning_vm_id, vm_name=req.vm_name)
        result = provisioner.provision_vm(req)
        result_payload = asdict(result)
        _task_log(
            "provision_vm_task",
            "provisioner returned",
            provisioning_vm_id=provisioning_vm_id,
            success=result.success,
            status=result.status,
            vm_name=result.vm_name or req.vm_name,
            public_ip=result.public_ip or "",
            error=result.error or "",
            step_elapsed=_elapsed_seconds(step_started),
            elapsed=_elapsed_seconds(task_started),
        )

        step_started = time.monotonic()
        _task_log(
            "provision_vm_task",
            "writing provisioning result to database",
            provisioning_vm_id=provisioning_vm_id,
        )
        with transaction.atomic():
            provisioning_vm = ProvisioningVM.objects.select_for_update().get(id=provisioning_vm_id, created_by_id=user_id)
            provisioning_vm.result_data = result_payload
            provisioning_vm.last_error = result.error
            if not result.success:
                _task_log(
                    "provision_vm_task",
                    "marking task FAILED",
                    provisioning_vm_id=provisioning_vm_id,
                    error=result.error or "",
                    elapsed=_elapsed_seconds(task_started),
                )
                provisioning_vm.update_execution_status(
                    status=ProvisioningVM.Status.FAILED,
                    modified_by_id=user_id,
                    last_error=result.error,
                )
                _append_event(provisioning_vm, "ERROR", "Provisioning failed.", {"error": result.error})
                return {"ok": False, "provisioning_vm_id": provisioning_vm_id, "error": result.error}

            vm_name = str(provisioning_vm.request_spec.get("inventory_vm_name") or result.vm_name or provisioning_vm.name)
            address = str(result.public_ip or provisioning_vm.request_spec.get("address", "")).strip()
            if not address:
                _task_log(
                    "provision_vm_task",
                    "missing address after provisioning",
                    provisioning_vm_id=provisioning_vm_id,
                    vm_name=vm_name,
                )
                raise RuntimeError("Provisioned VM has no public IP and no fallback address was provided.")

            inventory_step_started = time.monotonic()
            _task_log(
                "provision_vm_task",
                "upserting inventory VM",
                provisioning_vm_id=provisioning_vm_id,
                inventory_vm_name=vm_name,
                address=address,
                group_name=provisioning_vm.group_name or provisioning_vm.request_spec.get("group_name", "default"),
            )
            inventory_vm = _upsert_inventory_vm(
                provisioning_vm,
                vm_name=vm_name,
                address=address,
                result_data=result_payload,
            )
            _task_log(
                "provision_vm_task",
                "inventory VM upserted",
                provisioning_vm_id=provisioning_vm_id,
                inventory_vm_id=inventory_vm.id,
                step_elapsed=_elapsed_seconds(inventory_step_started),
                elapsed=_elapsed_seconds(task_started),
            )
            provisioning_vm.inventory_vm = inventory_vm
            provisioning_vm.last_error = ""
            provisioning_vm.status = ProvisioningVM.Status.SUCCEEDED
            provisioning_vm.instance_provider_name = provisioning_vm.provider
            provisioning_vm.instance_location = str(result.details.get("location") or req.location or "")
            provisioning_vm.instance_size = str(result.details.get("vm_size") or req.vm_size or "")
            provisioning_vm.instance_public_ip = address
            provisioning_vm.instance_power_state = ""
            provisioning_vm.instance_nic_name = _resource_name_from_id(
                str((result.resource_ids or {}).get("nic_id", ""))
            )
            provisioning_vm.modified_by_id = user_id
            provisioning_vm.save(
                update_fields=[
                    "result_data",
                    "last_error",
                    "inventory_vm",
                    "status",
                    "instance_provider_name",
                    "instance_location",
                    "instance_size",
                    "instance_public_ip",
                    "instance_power_state",
                    "instance_nic_name",
                    "modified_by",
                    "updated_at",
                ]
            )
        _task_log(
            "provision_vm_task",
            "database update complete",
            provisioning_vm_id=provisioning_vm_id,
            step_elapsed=_elapsed_seconds(step_started),
            elapsed=_elapsed_seconds(task_started),
        )
        _task_log(
            "provision_vm_task",
            "completed successfully",
            provisioning_vm_id=provisioning_vm_id,
            inventory_vm_id=provisioning_vm.inventory_vm_id or "",
            inventory_vm_name=vm_name,
            elapsed=_elapsed_seconds(task_started),
        )
        _append_event(
            provisioning_vm,
            "INFO",
            "Provisioning completed and VM added to inventory.",
            {"inventory_vm_id": provisioning_vm.inventory_vm_id},
        )
        return {"ok": True, "provisioning_vm_id": provisioning_vm_id, "inventory_vm_id": provisioning_vm.inventory_vm_id}
    except Exception as exc:
        _task_log(
            "provision_vm_task",
            "task crashed",
            provisioning_vm_id=provisioning_vm_id,
            error=str(exc),
            elapsed=_elapsed_seconds(task_started),
        )
        with transaction.atomic():
            provisioning_vm = ProvisioningVM.objects.select_for_update().get(id=provisioning_vm_id, created_by_id=user_id)
            provisioning_vm.update_execution_status(
                status=ProvisioningVM.Status.FAILED,
                modified_by_id=user_id,
                last_error=str(exc),
            )
        _append_event(provisioning_vm, "ERROR", "Provisioning task crashed.", {"error": str(exc)})
        return {"ok": False, "provisioning_vm_id": provisioning_vm_id, "error": str(exc)}


@shared_task(bind=True)
def deprovision_vm_task(self, *, provisioning_vm_id: int, user_id: int) -> dict:
    _task_log("deprovision_vm_task", "received task", provisioning_vm_id=provisioning_vm_id, user_id=user_id)
    with transaction.atomic():
        provisioning_vm = ProvisioningVM.objects.select_for_update().select_related("inventory_vm").get(
            id=provisioning_vm_id,
            created_by_id=user_id,
        )
        provisioning_vm.status = ProvisioningVM.Status.DELETING
        provisioning_vm.modified_by_id = user_id
        provisioning_vm.task_id = self.request.id or provisioning_vm.task_id
        provisioning_vm.save(update_fields=["status", "modified_by", "task_id", "updated_at"])
    _task_log("deprovision_vm_task", "status set to DELETING", provisioning_vm_id=provisioning_vm_id)
    _append_event(provisioning_vm, "INFO", "Deprovisioning started.")

    try:
        result_data = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
        resource_ids = result_data.get("resource_ids") or {}
        vm_name = str(result_data.get("vm_name") or provisioning_vm.request_spec.get("vm_name") or provisioning_vm.name)
        subscription_id = str(result_data.get("subscription_id", "")).strip()
        resource_group = str(result_data.get("resource_group", "") or provisioning_vm.request_spec.get("resource_group", "")).strip()
        if not subscription_id or not resource_group:
            raise ValueError("Cannot deprovision without subscription_id and resource_group from previous result.")

        creds = _resolve_credentials()
        provisioner = get_provisioner(provisioning_vm.provider, creds)
        delete_req = DeleteVMRequest(
            subscription_id=subscription_id,
            resource_group=resource_group,
            vm_name=vm_name,
            resource_ids=resource_ids,
            delete_attached_resources=True,
        )
        delete_result = provisioner.delete_vm(delete_req)
        delete_payload = asdict(delete_result)

        with transaction.atomic():
            provisioning_vm = ProvisioningVM.objects.select_for_update().select_related("inventory_vm").get(
                id=provisioning_vm_id,
                created_by_id=user_id,
            )
            current = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
            provisioning_vm.result_data = {**current, "delete_result": delete_payload}
            provisioning_vm.last_error = delete_result.error
            if not delete_result.success:
                provisioning_vm.update_execution_status(
                    status=ProvisioningVM.Status.FAILED,
                    modified_by_id=user_id,
                    last_error=delete_result.error,
                )
                _append_event(provisioning_vm, "ERROR", "Deprovisioning failed.", {"error": delete_result.error})
                return {"ok": False, "provisioning_vm_id": provisioning_vm_id, "error": delete_result.error}

            linked_vm = provisioning_vm.inventory_vm
            if linked_vm is not None:
                linked_vm.status = VM.VMStatus.INACTIVE
                linked_vm.archived = True
                metadata = linked_vm.metadata if isinstance(linked_vm.metadata, dict) else {}
                metadata["deprovisioned"] = True
                linked_vm.metadata = metadata
                linked_vm.modified_by_id = user_id
                linked_vm.save(update_fields=["status", "archived", "metadata", "modified_by", "updated_at"])

            provisioning_vm.status = ProvisioningVM.Status.DELETED
            provisioning_vm.last_error = ""
            provisioning_vm.modified_by_id = user_id
            provisioning_vm.save(update_fields=["result_data", "last_error", "status", "modified_by", "updated_at"])
        _append_event(provisioning_vm, "INFO", "Deprovisioning completed and inventory VM archived.")
        return {"ok": True, "provisioning_vm_id": provisioning_vm_id}
    except Exception as exc:
        with transaction.atomic():
            provisioning_vm = ProvisioningVM.objects.select_for_update().get(id=provisioning_vm_id, created_by_id=user_id)
            provisioning_vm.update_execution_status(
                status=ProvisioningVM.Status.FAILED,
                modified_by_id=user_id,
                last_error=str(exc),
            )
        _append_event(provisioning_vm, "ERROR", "Deprovisioning task crashed.", {"error": str(exc)})
        return {"ok": False, "provisioning_vm_id": provisioning_vm_id, "error": str(exc)}


@shared_task(bind=True)
def stop_provisioning_vm_task(self, *, stop_request_id: int, provisioning_vm_id: int, user_id: int) -> dict:
    _task_log(
        "stop_provisioning_vm_task",
        "received task",
        provisioning_vm_id=provisioning_vm_id,
        stop_request_id=stop_request_id,
        user_id=user_id,
    )
    with transaction.atomic():
        stop_request = ProvisioningVMStopRequest.objects.select_for_update().select_related("provisioning_vm").get(
            id=stop_request_id,
            provisioning_vm_id=provisioning_vm_id,
            created_by_id=user_id,
        )
        stop_request.update_stop_status(
            status=ProvisioningVMStopRequest.Status.RUNNING,
            modified_by_id=user_id,
            last_error="",
        )

    try:
        _task_log("stop_provisioning_vm_task", "resolving Azure credentials", provisioning_vm_id=provisioning_vm_id)
        creds = _resolve_credentials()
        provisioning_vm = ProvisioningVM.objects.get(id=provisioning_vm_id, created_by_id=user_id)
        current_result = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
        spec = provisioning_vm.request_spec if isinstance(provisioning_vm.request_spec, dict) else {}
        resource_ids = current_result.get("resource_ids") if isinstance(current_result.get("resource_ids"), dict) else {}
        resource_group = str(current_result.get("resource_group") or spec.get("resource_group") or "").strip()
        vm_name = str(current_result.get("vm_name") or spec.get("vm_name") or provisioning_vm.name).strip()
        subscription_selector = str(spec.get("subscription_selector") or current_result.get("subscription_id") or "").strip()
        if not resource_group:
            raise ValueError("resource_group is required to stop VM.")
        if not vm_name:
            raise ValueError("vm_name is required to stop VM.")
        stop_req = StopVMRequest(
            subscription_selector=subscription_selector,
            subscription_id=str(current_result.get("subscription_id", "")).strip(),
            resource_group=resource_group,
            vm_name=vm_name,
            vm_id=str(resource_ids.get("vm_id", "")).strip(),
            deallocate=True,
        )
        _task_log(
            "stop_provisioning_vm_task",
            "stop request ready",
            provisioning_vm_id=provisioning_vm_id,
            vm_name=stop_req.vm_name,
            resource_group=stop_req.resource_group,
            subscription_id=stop_req.subscription_id or "<resolve-by-selector>",
        )
        provisioner = get_provisioner(provisioning_vm.provider, creds)
        if not hasattr(provisioner, "stop_vm"):
            raise ValueError(f"Provider '{provisioning_vm.provider}' does not support VM stop yet.")
        stop_result = provisioner.stop_vm(stop_req)
        stop_payload = asdict(stop_result)
        _task_log(
            "stop_provisioning_vm_task",
            "stop result",
            provisioning_vm_id=provisioning_vm_id,
            success=stop_result.success,
            status=stop_result.status,
            power_state=stop_result.power_state or "",
            error=stop_result.error or "",
        )

        lookup_result: dict = {}
        if stop_result.success and hasattr(provisioner, "get_vm_instance_view"):
            subscription_selector = stop_result.subscription_id or stop_req.subscription_id or stop_req.subscription_selector
            if subscription_selector:
                lookup_result = provisioner.get_vm_instance_view(
                    subscription_selector=subscription_selector,
                    resource_group=stop_req.resource_group,
                    vm_name=stop_req.vm_name,
                )

        with transaction.atomic():
            stop_request = ProvisioningVMStopRequest.objects.select_for_update().get(
                id=stop_request_id,
                provisioning_vm_id=provisioning_vm_id,
                created_by_id=user_id,
            )
            stop_data = {"stop_result": stop_payload}
            if lookup_result:
                stop_data["vm_lookup"] = lookup_result
            stop_status = ProvisioningVMStopRequest.Status.PASS if stop_result.success else ProvisioningVMStopRequest.Status.FAIL
            stop_request.update_stop_status(
                status=stop_status,
                modified_by_id=user_id,
                last_error="" if stop_result.success else str(stop_result.error or "Stop VM failed."),
                stop_data=stop_data,
            )

            provisioning_vm = ProvisioningVM.objects.select_for_update().select_related("inventory_vm").get(
                id=provisioning_vm_id,
                created_by_id=user_id,
            )
            new_result = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
            new_result["stop_result"] = stop_payload
            if lookup_result:
                new_result["check_snapshot_after_stop"] = lookup_result
            provisioning_vm.result_data = new_result

            if stop_result.success:
                vm_data = lookup_result.get("vm") if isinstance(lookup_result, dict) else {}
                if not isinstance(vm_data, dict):
                    vm_data = {}
                stop_details = stop_result.details if isinstance(stop_result.details, dict) else {}
                power_state = str(vm_data.get("power_state") or stop_result.power_state or "stopped")
                location = str(vm_data.get("location") or stop_details.get("location") or "")
                size = str(vm_data.get("vm_size") or stop_details.get("vm_size") or "")
                public_ips = vm_data.get("public_ips") if isinstance(vm_data.get("public_ips"), list) else []
                network_interfaces = vm_data.get("network_interfaces") if isinstance(vm_data.get("network_interfaces"), list) else []
                public_ip = str(public_ips[0] if public_ips else provisioning_vm.instance_public_ip)
                nic_name = str(network_interfaces[0] if network_interfaces else provisioning_vm.instance_nic_name)
                provisioning_vm.instance_provider_name = provisioning_vm.provider
                provisioning_vm.instance_location = location
                provisioning_vm.instance_size = size
                provisioning_vm.instance_power_state = power_state
                provisioning_vm.instance_public_ip = public_ip
                provisioning_vm.instance_nic_name = nic_name
                provisioning_vm.last_error = ""

                linked_vm = provisioning_vm.inventory_vm
                if linked_vm is not None:
                    linked_vm.status = VM.VMStatus.INACTIVE
                    metadata = linked_vm.metadata if isinstance(linked_vm.metadata, dict) else {}
                    metadata["cloud_power_state"] = power_state
                    metadata["cloud_last_action"] = "stop_vm"
                    linked_vm.metadata = metadata
                    linked_vm.modified_by_id = user_id
                    linked_vm.save(update_fields=["status", "metadata", "modified_by", "updated_at"])
            else:
                provisioning_vm.last_error = str(stop_result.error or "Stop VM failed.")
            provisioning_vm.modified_by_id = user_id
            provisioning_vm.save(
                update_fields=[
                    "result_data",
                    "instance_provider_name",
                    "instance_location",
                    "instance_size",
                    "instance_power_state",
                    "instance_public_ip",
                    "instance_nic_name",
                    "last_error",
                    "modified_by",
                    "updated_at",
                ]
            )

        if stop_result.success:
            _append_event(
                provisioning_vm,
                "INFO",
                "Stop VM request passed.",
                {"stop_request_id": stop_request_id, "power_state": stop_result.power_state},
            )
            return {"ok": True, "stop_request_id": stop_request_id}

        _append_event(
            provisioning_vm,
            "ERROR",
            "Stop VM request failed.",
            {"stop_request_id": stop_request_id, "error": stop_result.error},
        )
        return {"ok": False, "stop_request_id": stop_request_id, "error": stop_result.error}
    except Exception as exc:
        with transaction.atomic():
            stop_request = ProvisioningVMStopRequest.objects.select_for_update().get(
                id=stop_request_id,
                provisioning_vm_id=provisioning_vm_id,
                created_by_id=user_id,
            )
            stop_request.update_stop_status(
                status=ProvisioningVMStopRequest.Status.FAIL,
                modified_by_id=user_id,
                last_error=str(exc),
                stop_data={},
            )
        provisioning_vm = ProvisioningVM.objects.filter(id=provisioning_vm_id, created_by_id=user_id).first()
        if provisioning_vm is not None:
            _append_event(provisioning_vm, "ERROR", "Stop VM request failed.", {"error": str(exc)})
        _task_log(
            "stop_provisioning_vm_task",
            "task crashed",
            provisioning_vm_id=provisioning_vm_id,
            stop_request_id=stop_request_id,
            error=str(exc),
        )
        return {"ok": False, "stop_request_id": stop_request_id, "error": str(exc)}


@shared_task(bind=True)
def start_provisioning_vm_task(self, *, start_request_id: int, provisioning_vm_id: int, user_id: int) -> dict:
    _task_log(
        "start_provisioning_vm_task",
        "received task",
        provisioning_vm_id=provisioning_vm_id,
        start_request_id=start_request_id,
        user_id=user_id,
    )
    with transaction.atomic():
        start_request = ProvisioningVMStartRequest.objects.select_for_update().select_related("provisioning_vm").get(
            id=start_request_id,
            provisioning_vm_id=provisioning_vm_id,
            created_by_id=user_id,
        )
        start_request.update_start_status(
            status=ProvisioningVMStartRequest.Status.RUNNING,
            modified_by_id=user_id,
            last_error="",
        )

    try:
        _task_log("start_provisioning_vm_task", "resolving Azure credentials", provisioning_vm_id=provisioning_vm_id)
        creds = _resolve_credentials()
        provisioning_vm = ProvisioningVM.objects.get(id=provisioning_vm_id, created_by_id=user_id)
        current_result = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
        spec = provisioning_vm.request_spec if isinstance(provisioning_vm.request_spec, dict) else {}
        resource_ids = current_result.get("resource_ids") if isinstance(current_result.get("resource_ids"), dict) else {}
        resource_group = str(current_result.get("resource_group") or spec.get("resource_group") or "").strip()
        vm_name = str(current_result.get("vm_name") or spec.get("vm_name") or provisioning_vm.name).strip()
        subscription_selector = str(spec.get("subscription_selector") or current_result.get("subscription_id") or "").strip()
        if not resource_group:
            raise ValueError("resource_group is required to start VM.")
        if not vm_name:
            raise ValueError("vm_name is required to start VM.")
        start_req = StartVMRequest(
            subscription_selector=subscription_selector,
            subscription_id=str(current_result.get("subscription_id", "")).strip(),
            resource_group=resource_group,
            vm_name=vm_name,
            vm_id=str(resource_ids.get("vm_id", "")).strip(),
        )
        _task_log(
            "start_provisioning_vm_task",
            "start request ready",
            provisioning_vm_id=provisioning_vm_id,
            vm_name=start_req.vm_name,
            resource_group=start_req.resource_group,
            subscription_id=start_req.subscription_id or "<resolve-by-selector>",
        )
        provisioner = get_provisioner(provisioning_vm.provider, creds)
        if not hasattr(provisioner, "start_vm"):
            raise ValueError(f"Provider '{provisioning_vm.provider}' does not support VM start yet.")
        start_result = provisioner.start_vm(start_req)
        start_payload = asdict(start_result)
        _task_log(
            "start_provisioning_vm_task",
            "start result",
            provisioning_vm_id=provisioning_vm_id,
            success=start_result.success,
            status=start_result.status,
            power_state=start_result.power_state or "",
            error=start_result.error or "",
        )

        lookup_result: dict = {}
        if start_result.success and hasattr(provisioner, "get_vm_instance_view"):
            selector = start_result.subscription_id or start_req.subscription_id or start_req.subscription_selector
            if selector:
                lookup_result = provisioner.get_vm_instance_view(
                    subscription_selector=selector,
                    resource_group=start_req.resource_group,
                    vm_name=start_req.vm_name,
                )

        with transaction.atomic():
            start_request = ProvisioningVMStartRequest.objects.select_for_update().get(
                id=start_request_id,
                provisioning_vm_id=provisioning_vm_id,
                created_by_id=user_id,
            )
            start_data = {"start_result": start_payload}
            if lookup_result:
                start_data["vm_lookup"] = lookup_result
            start_status = ProvisioningVMStartRequest.Status.PASS if start_result.success else ProvisioningVMStartRequest.Status.FAIL
            start_request.update_start_status(
                status=start_status,
                modified_by_id=user_id,
                last_error="" if start_result.success else str(start_result.error or "Start VM failed."),
                start_data=start_data,
            )

            provisioning_vm = ProvisioningVM.objects.select_for_update().select_related("inventory_vm").get(
                id=provisioning_vm_id,
                created_by_id=user_id,
            )
            new_result = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
            new_result["start_result"] = start_payload
            if lookup_result:
                new_result["check_snapshot_after_start"] = lookup_result
            provisioning_vm.result_data = new_result

            if start_result.success:
                vm_data = lookup_result.get("vm") if isinstance(lookup_result, dict) else {}
                if not isinstance(vm_data, dict):
                    vm_data = {}
                start_details = start_result.details if isinstance(start_result.details, dict) else {}
                power_state = str(vm_data.get("power_state") or start_result.power_state or "running")
                location = str(vm_data.get("location") or start_details.get("location") or "")
                size = str(vm_data.get("vm_size") or start_details.get("vm_size") or "")
                public_ips = vm_data.get("public_ips") if isinstance(vm_data.get("public_ips"), list) else []
                network_interfaces = vm_data.get("network_interfaces") if isinstance(vm_data.get("network_interfaces"), list) else []
                public_ip = str(public_ips[0] if public_ips else provisioning_vm.instance_public_ip)
                nic_name = str(network_interfaces[0] if network_interfaces else provisioning_vm.instance_nic_name)
                provisioning_vm.instance_provider_name = provisioning_vm.provider
                provisioning_vm.instance_location = location
                provisioning_vm.instance_size = size
                provisioning_vm.instance_power_state = power_state
                provisioning_vm.instance_public_ip = public_ip
                provisioning_vm.instance_nic_name = nic_name
                provisioning_vm.last_error = ""
                if provisioning_vm.status in (
                    ProvisioningVM.Status.FAILED,
                    ProvisioningVM.Status.SUCCEEDED,
                ):
                    provisioning_vm.status = ProvisioningVM.Status.SUCCEEDED

                linked_vm = provisioning_vm.inventory_vm
                if linked_vm is not None:
                    linked_vm.status = VM.VMStatus.ACTIVE
                    metadata = linked_vm.metadata if isinstance(linked_vm.metadata, dict) else {}
                    metadata["cloud_power_state"] = power_state
                    metadata["cloud_last_action"] = "start_vm"
                    linked_vm.metadata = metadata
                    linked_vm.modified_by_id = user_id
                    linked_vm.save(update_fields=["status", "metadata", "modified_by", "updated_at"])
            else:
                provisioning_vm.last_error = str(start_result.error or "Start VM failed.")
            provisioning_vm.modified_by_id = user_id
            provisioning_vm.save(
                update_fields=[
                    "result_data",
                    "status",
                    "instance_provider_name",
                    "instance_location",
                    "instance_size",
                    "instance_power_state",
                    "instance_public_ip",
                    "instance_nic_name",
                    "last_error",
                    "modified_by",
                    "updated_at",
                ]
            )

        if start_result.success:
            _append_event(
                provisioning_vm,
                "INFO",
                "Start VM request passed.",
                {"start_request_id": start_request_id, "power_state": start_result.power_state},
            )
            return {"ok": True, "start_request_id": start_request_id}

        _append_event(
            provisioning_vm,
            "ERROR",
            "Start VM request failed.",
            {"start_request_id": start_request_id, "error": start_result.error},
        )
        return {"ok": False, "start_request_id": start_request_id, "error": start_result.error}
    except Exception as exc:
        with transaction.atomic():
            start_request = ProvisioningVMStartRequest.objects.select_for_update().get(
                id=start_request_id,
                provisioning_vm_id=provisioning_vm_id,
                created_by_id=user_id,
            )
            start_request.update_start_status(
                status=ProvisioningVMStartRequest.Status.FAIL,
                modified_by_id=user_id,
                last_error=str(exc),
                start_data={},
            )
        provisioning_vm = ProvisioningVM.objects.filter(id=provisioning_vm_id, created_by_id=user_id).first()
        if provisioning_vm is not None:
            _append_event(provisioning_vm, "ERROR", "Start VM request failed.", {"error": str(exc)})
        _task_log(
            "start_provisioning_vm_task",
            "task crashed",
            provisioning_vm_id=provisioning_vm_id,
            start_request_id=start_request_id,
            error=str(exc),
        )
        return {"ok": False, "start_request_id": start_request_id, "error": str(exc)}


@shared_task(bind=True)
def check_provisioning_vm_task(self, *, check_request_id: int, provisioning_vm_id: int, user_id: int) -> dict:
    _task_log(
        "check_provisioning_vm_task",
        "received task",
        provisioning_vm_id=provisioning_vm_id,
        check_request_id=check_request_id,
        user_id=user_id,
    )
    with transaction.atomic():
        check_request = ProvisioningVMCheckRequest.objects.select_for_update().select_related("provisioning_vm").get(
            id=check_request_id,
            provisioning_vm_id=provisioning_vm_id,
            created_by_id=user_id,
        )
        check_request.update_check_status(
            status=ProvisioningVMCheckRequest.Status.RUNNING,
            modified_by_id=user_id,
            last_error="",
        )

    try:
        _task_log("check_provisioning_vm_task", "resolving Azure credentials", provisioning_vm_id=provisioning_vm_id)
        creds = _resolve_credentials()
        provisioning_vm = ProvisioningVM.objects.get(id=provisioning_vm_id, created_by_id=user_id)
        req = _build_provision_request(provisioning_vm, creds)
        _task_log(
            "check_provisioning_vm_task",
            "request ready",
            provisioning_vm_id=provisioning_vm_id,
            resource_group=req.resource_group,
            vm_name=req.vm_name,
            subscription_selector=req.subscription_selector,
        )
        provisioner = get_provisioner(provisioning_vm.provider, creds)
        if not hasattr(provisioner, "get_vm_instance_view"):
            raise ValueError(f"Provider '{provisioning_vm.provider}' does not support VM check yet.")

        lookup_result = provisioner.get_vm_instance_view(
            subscription_selector=req.subscription_selector,
            resource_group=req.resource_group,
            vm_name=req.vm_name,
        )
        check_data = {
            "provider": provisioning_vm.provider,
            "credentials_ready": True,
            "request_spec_ready": True,
            "lookup_success": bool(lookup_result.get("success")),
            "cloud_vm_exists": bool(lookup_result.get("exists")),
            "subscription_id": str(lookup_result.get("subscription_id", "")),
            "resource_group": str(lookup_result.get("resource_group", req.resource_group)),
            "vm_name": str(lookup_result.get("vm_name", req.vm_name)),
            "vm": lookup_result.get("vm") or {},
        }
        vm_data = check_data["vm"] if isinstance(check_data["vm"], dict) else {}
        if vm_data:
            check_data.update(
                {
                    "vm_id": str(vm_data.get("id", "")),
                    "provisioning_state": str(vm_data.get("provisioning_state", "")),
                    "power_state": str(vm_data.get("power_state", "")),
                    "location": str(vm_data.get("location", "")),
                    "vm_size": str(vm_data.get("vm_size", "")),
                    "public_ips": vm_data.get("public_ips") or [],
                    "private_ips": vm_data.get("private_ips") or [],
                    "network_interfaces": vm_data.get("network_interfaces") or [],
                    "network_security_groups": vm_data.get("network_security_groups") or [],
                }
            )

        cloud_exists = bool(check_data.get("cloud_vm_exists"))
        provisioning_state = str(check_data.get("provisioning_state", "")).strip()
        provisioning_succeeded = provisioning_state.lower() == "succeeded"
        cloud_ready = bool(cloud_exists and provisioning_succeeded)
        check_error = str(lookup_result.get("error", "")).strip()
        if not check_error:
            if not cloud_exists:
                check_error = "Cloud VM not found."
            elif not cloud_ready:
                check_error = (
                    "Cloud VM exists but provisioning state is "
                    f"'{provisioning_state or '<unknown>'}' (expected 'Succeeded')."
                )
        _task_log(
            "check_provisioning_vm_task",
            "lookup result",
            provisioning_vm_id=provisioning_vm_id,
            check_request_id=check_request_id,
            cloud_exists=cloud_exists,
            provisioning_state=provisioning_state or "<unknown>",
            cloud_ready=cloud_ready,
            error=check_error or "",
        )
        check_status = ProvisioningVMCheckRequest.Status.PASS if cloud_ready else ProvisioningVMCheckRequest.Status.FAIL

        with transaction.atomic():
            check_request = ProvisioningVMCheckRequest.objects.select_for_update().get(
                id=check_request_id,
                provisioning_vm_id=provisioning_vm_id,
                created_by_id=user_id,
            )
            check_request.update_check_status(
                status=check_status,
                modified_by_id=user_id,
                check_data=check_data,
                last_error="" if cloud_ready else check_error,
            )
            provisioning_vm = ProvisioningVM.objects.select_for_update().get(id=provisioning_vm_id, created_by_id=user_id)
            if cloud_exists:
                current_result = provisioning_vm.result_data if isinstance(provisioning_vm.result_data, dict) else {}
                current_result.update(
                    {
                        "subscription_id": check_data.get("subscription_id", ""),
                        "resource_group": check_data.get("resource_group", ""),
                        "vm_name": check_data.get("vm_name", ""),
                        "vm_id": check_data.get("vm_id", ""),
                        "check_snapshot": check_data,
                    }
                )
                resource_ids = current_result.get("resource_ids")
                if not isinstance(resource_ids, dict):
                    resource_ids = {}
                if check_data.get("vm_id"):
                    resource_ids["vm_id"] = check_data["vm_id"]
                current_result["resource_ids"] = resource_ids
                provisioning_vm.result_data = current_result
                inventory_candidate = {
                    "vm_name": str(
                        provisioning_vm.request_spec.get("inventory_vm_name")
                        or check_data.get("vm_name")
                        or provisioning_vm.name
                    ),
                    "address": str(provisioning_vm.request_spec.get("address", "")).strip(),
                    "user_name": str(provisioning_vm.request_spec.get("admin_username", "ubuntu")),
                    "provider_name": str(check_data.get("provider", provisioning_vm.provider)),
                    "location": str(check_data.get("location", "")),
                    "size": str(check_data.get("vm_size", "")),
                    "power_state": str(check_data.get("power_state", "")),
                    "public_ip": "",
                    "nic_name": "",
                }
                inventory_candidate["can_create_inventory_vm"] = bool(
                    str(inventory_candidate.get("vm_name", "")).strip()
                    and str(inventory_candidate.get("address", "")).strip()
                )
                if hasattr(provisioner, "build_inventory_vm_candidate"):
                    inventory_candidate = provisioner.build_inventory_vm_candidate(
                        vm_lookup=lookup_result,
                        default_vm_name=inventory_candidate["vm_name"],
                        default_user_name=inventory_candidate["user_name"],
                        fallback_address=inventory_candidate["address"],
                    )
                provisioning_vm.instance_provider_name = str(inventory_candidate.get("provider_name", provisioning_vm.provider))
                provisioning_vm.instance_location = str(inventory_candidate.get("location", ""))
                provisioning_vm.instance_size = str(inventory_candidate.get("size", ""))
                provisioning_vm.instance_power_state = str(inventory_candidate.get("power_state", ""))
                provisioning_vm.instance_public_ip = str(inventory_candidate.get("public_ip", ""))
                provisioning_vm.instance_nic_name = str(inventory_candidate.get("nic_name", ""))
                if cloud_ready:
                    if provisioning_vm.status in (
                        ProvisioningVM.Status.INITIALIZED,
                        ProvisioningVM.Status.PENDING,
                        ProvisioningVM.Status.RUNNING,
                    ):
                        provisioning_vm.update_execution_status(
                            status=ProvisioningVM.Status.SUCCEEDED,
                            modified_by_id=user_id,
                            last_error="",
                        )
                    if bool(inventory_candidate.get("can_create_inventory_vm")):
                        inventory_vm_name = str(inventory_candidate.get("vm_name", "")).strip()
                        address = str(inventory_candidate.get("address", "")).strip()
                        inventory_vm = _upsert_inventory_vm(
                            provisioning_vm,
                            vm_name=inventory_vm_name,
                            address=address,
                            result_data=current_result,
                        )
                        provisioning_vm.inventory_vm = inventory_vm
                provisioning_vm.last_error = ""
                provisioning_vm.modified_by_id = user_id
                provisioning_vm.save(
                    update_fields=[
                        "result_data",
                        "inventory_vm",
                        "instance_provider_name",
                        "instance_location",
                        "instance_size",
                        "instance_power_state",
                        "instance_public_ip",
                        "instance_nic_name",
                        "last_error",
                        "modified_by",
                        "updated_at",
                    ]
                )

        if cloud_ready:
            _append_event(provisioning_vm, "INFO", "Provisioning check request passed.", check_data)
            return {"ok": True, "check_request_id": check_request_id}
        _append_event(provisioning_vm, "ERROR", "Provisioning check request failed.", check_data)
        return {"ok": False, "check_request_id": check_request_id, "error": check_error}
    except Exception as exc:
        with transaction.atomic():
            check_request = ProvisioningVMCheckRequest.objects.select_for_update().get(
                id=check_request_id,
                provisioning_vm_id=provisioning_vm_id,
                created_by_id=user_id,
            )
            check_request.update_check_status(
                status=ProvisioningVMCheckRequest.Status.FAIL,
                modified_by_id=user_id,
                last_error=str(exc),
                check_data={"credentials_ready": False},
            )
        provisioning_vm = ProvisioningVM.objects.filter(id=provisioning_vm_id, created_by_id=user_id).first()
        if provisioning_vm is not None:
            _append_event(provisioning_vm, "ERROR", "Provisioning check request failed.", {"error": str(exc)})
        return {"ok": False, "check_request_id": check_request_id, "error": str(exc)}

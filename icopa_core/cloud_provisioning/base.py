"""Cloud provisioning interfaces and request/response contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol


@dataclass(slots=True)
class ProvisionVMRequest:
    """Normalized VM provisioning request."""

    subscription_selector: str
    resource_group: str
    location: str
    vm_name: str
    vm_size: str
    admin_username: str
    ssh_public_key: str
    image: Dict[str, str]
    network: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    custom_data: str = ""
    spot_enabled: bool = False
    spot_eviction_policy: str = "Deallocate"
    spot_max_price: float | None = None
    os_disk_delete_option: str = "Delete"
    data_disk_delete_option: str = "Delete"


@dataclass(slots=True)
class ProvisionVMResult:
    """Provisioning result payload."""

    success: bool
    provider: str
    status: str
    vm_id: str = ""
    vm_name: str = ""
    public_ip: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    resource_ids: Dict[str, str] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class DeleteVMRequest:
    """Normalized VM deletion request."""

    subscription_id: str
    resource_group: str
    vm_name: str
    resource_ids: Dict[str, str] = field(default_factory=dict)
    delete_attached_resources: bool = True


@dataclass(slots=True)
class DeleteVMResult:
    """Delete result payload."""

    success: bool
    provider: str
    status: str
    vm_name: str = ""
    deleted_resources: Dict[str, bool] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class StopVMRequest:
    """Normalized VM stop request."""

    resource_group: str
    vm_name: str
    subscription_selector: str = ""
    subscription_id: str = ""
    vm_id: str = ""
    deallocate: bool = True


@dataclass(slots=True)
class StopVMResult:
    """Stop result payload."""

    success: bool
    provider: str
    status: str
    vm_name: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    vm_id: str = ""
    power_state: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class StartVMRequest:
    """Normalized VM start request."""

    resource_group: str
    vm_name: str
    subscription_selector: str = ""
    subscription_id: str = ""
    vm_id: str = ""


@dataclass(slots=True)
class StartVMResult:
    """Start result payload."""

    success: bool
    provider: str
    status: str
    vm_name: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    vm_id: str = ""
    power_state: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class CloudProvisioner(Protocol):
    """Provider contract."""

    def provision_vm(self, req: ProvisionVMRequest) -> ProvisionVMResult:
        """Provision one VM from a normalized request."""

    def delete_vm(self, req: DeleteVMRequest) -> DeleteVMResult:
        """Delete one VM and optionally attached resources."""

    def stop_vm(self, req: StopVMRequest) -> StopVMResult:
        """Stop one VM instance (deallocate or power off)."""

    def start_vm(self, req: StartVMRequest) -> StartVMResult:
        """Start one VM instance."""

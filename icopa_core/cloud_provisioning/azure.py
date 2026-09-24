"""Azure cloud provisioner implementation."""

from __future__ import annotations

import base64
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from .base import (
    DeleteVMRequest,
    DeleteVMResult,
    ProvisionVMRequest,
    ProvisionVMResult,
    StartVMRequest,
    StartVMResult,
    StopVMRequest,
    StopVMResult,
)

TOKEN_SCOPE = "https://management.azure.com/.default"
SUBSCRIPTIONS_API_VERSION = "2020-01-01"
RESOURCE_GROUPS_API_VERSION = "2022-09-01"
COMPUTE_SKUS_API_VERSION = "2021-07-01"
NETWORK_API_VERSION = "2023-09-01"
COMPUTE_VM_API_VERSION = "2023-09-01"


def _build_cloud_init_from_vm_config(vm_cfg: dict) -> str:
    """Build base64 cloud-init payload from vm config (post_setup/container)."""
    post_setup = vm_cfg.get("post_setup") or {}
    container = vm_cfg.get("container") or {}
    install_docker = bool(post_setup.get("install_docker", False))
    container_image = str(container.get("image", "")).strip()
    if not install_docker and not container_image:
        return ""

    admin_username = str(vm_cfg.get("admin_username") or "ubuntu").strip() or "ubuntu"
    container_name = str(container.get("name") or "icopa-runtime").strip() or "icopa-runtime"
    run_args = str(container.get("run_args") or "--network host --restart unless-stopped").strip()

    packages = ["docker.io"]
    commands = [
        "systemctl enable --now docker",
        "getent group docker >/dev/null || groupadd docker",
        f"usermod -aG docker {admin_username}",
    ]
    if container_image:
        commands.extend(
            [
                f"docker rm -f {container_name} || true",
                f"docker pull {container_image}",
                f"docker run -d --name {container_name} {run_args} {container_image}",
            ]
        )

    cloud_init = "#cloud-config\npackage_update: true\npackages:\n"
    for package in packages:
        cloud_init += f"  - {package}\n"
    cloud_init += "runcmd:\n"
    for cmd in commands:
        cloud_init += f"  - {cmd}\n"
    return base64.b64encode(cloud_init.encode("utf-8")).decode("utf-8")


def build_provision_request_from_vm_config(
    vm_cfg: dict,
    *,
    default_subscription_selector: str = "",
    default_vm_name: str = "icopa-vm",
) -> ProvisionVMRequest:
    """
    Build a provisioning request from a VM configuration.
    """
    subscription_selector = str(
        vm_cfg.get("subscription_selector")
        or vm_cfg.get("subscription_id")
        or default_subscription_selector
        or ""
    ).strip()
    resource_group = str(vm_cfg.get("resource_group", "")).strip()
    location = str(vm_cfg.get("location", "")).strip()
    vm_name = str(vm_cfg.get("name") or vm_cfg.get("vm_name") or default_vm_name).strip()
    vm_size = str(vm_cfg.get("size") or vm_cfg.get("vm_size") or "").strip()
    admin_username = str(vm_cfg.get("admin_username") or "ubuntu").strip() or "ubuntu"
    ssh_public_key = str(vm_cfg.get("ssh_public_key") or "").strip()
    image = vm_cfg.get("image") or {}
    network = vm_cfg.get("network") or {}
    spot = vm_cfg.get("spot") or {}
    storage = vm_cfg.get("storage") or {}
    tags = vm_cfg.get("tags") or {}
    custom_data = str(vm_cfg.get("custom_data") or "").strip() or _build_cloud_init_from_vm_config(vm_cfg)
    spot_enabled = bool(
        spot.get("enabled", False)
        if isinstance(spot, dict)
        else vm_cfg.get("spot_enabled", False)
    )
    spot_eviction_policy = str(
        (spot.get("eviction_policy") if isinstance(spot, dict) else vm_cfg.get("spot_eviction_policy"))
        or vm_cfg.get("eviction_policy")
        or "Deallocate"
    ).strip()
    spot_max_price_raw = (
        spot.get("max_price")
        if isinstance(spot, dict)
        else vm_cfg.get("spot_max_price")
    )
    spot_max_price: float | None = None
    if spot_max_price_raw not in (None, ""):
        spot_max_price = float(spot_max_price_raw)
    os_disk_delete_option = str(
        (storage.get("os_disk_delete_option") if isinstance(storage, dict) else vm_cfg.get("os_disk_delete_option"))
        or vm_cfg.get("disk_delete_option")
        or "Delete"
    ).strip()
    data_disk_delete_option = str(
        (storage.get("data_disk_delete_option") if isinstance(storage, dict) else vm_cfg.get("data_disk_delete_option"))
        or vm_cfg.get("disk_delete_option")
        or "Delete"
    ).strip()

    return ProvisionVMRequest(
        subscription_selector=subscription_selector,
        resource_group=resource_group,
        location=location,
        vm_name=vm_name,
        vm_size=vm_size,
        admin_username=admin_username,
        ssh_public_key=ssh_public_key,
        image=image if isinstance(image, dict) else {},
        network=network if isinstance(network, dict) else {},
        tags=tags if isinstance(tags, dict) else {},
        custom_data=custom_data,
        spot_enabled=spot_enabled,
        spot_eviction_policy=spot_eviction_policy,
        spot_max_price=spot_max_price,
        os_disk_delete_option=os_disk_delete_option,
        data_disk_delete_option=data_disk_delete_option,
    )


class AzureProvisioner:
    """Provisioner backed by Azure Resource Manager REST APIs."""

    def __init__(
        self,
        credential: dict,
        *,
        timeout: int = 30,
        poll_interval: int = 5,
        max_poll_seconds: int = 900,
        progress_callback: Optional[Callable[[str], None]] = None,
    ):
        self.credential = credential
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds
        self.progress_callback = progress_callback

    def provision_vm(self, req: ProvisionVMRequest) -> ProvisionVMResult:
        try:
            self._progress("Requesting Azure access token.")
            token = self._get_access_token()
            self._progress("Resolving subscription.")
            subscription_id = self._resolve_subscription_id(req.subscription_selector, token)
            location = self._normalize_region(req.location)

            self._progress("Resolving existing VNet/subnet.")
            subnet_id, vnet_location = self._resolve_existing_subnet(
                token=token,
                subscription_id=subscription_id,
                resource_group=req.resource_group,
                vnet_name=str(req.network.get("vnet_name", f"{req.vm_name}-vnet")),
                subnet_name=str(req.network.get("subnet_name", "default")),
            )
            if vnet_location:
                if vnet_location != location:
                    self._progress(
                        f"Using VNet region '{vnet_location}' instead of configured '{location}'."
                    )
                location = vnet_location

            self._progress(f"Checking VM size availability: {req.vm_size} in {location}.")
            if not self._check_vm_size_available(token, subscription_id, location, req.vm_size):
                raise RuntimeError(
                    f"VM size '{req.vm_size}' is not available in '{location}' for this subscription."
                )

            rg_scope = f"/subscriptions/{subscription_id}/resourceGroups/{req.resource_group}"
            nsg_name = str(req.network.get("nsg_name", f"{req.vm_name}-nsg"))
            pip_name = str(req.network.get("public_ip_name", f"{req.vm_name}-pip"))
            nic_name = str(req.network.get("nic_name", f"{req.vm_name}-nic"))
            create_public_ip = bool(req.network.get("create_public_ip", True))
            enable_public_ssh = bool(req.network.get("enable_public_ssh", create_public_ip))

            nsg_id = f"{rg_scope}/providers/Microsoft.Network/networkSecurityGroups/{nsg_name}"
            pip_id = f"{rg_scope}/providers/Microsoft.Network/publicIPAddresses/{pip_name}"
            nic_id = f"{rg_scope}/providers/Microsoft.Network/networkInterfaces/{nic_name}"
            vm_id = f"{rg_scope}/providers/Microsoft.Compute/virtualMachines/{req.vm_name}"

            if enable_public_ssh:
                nsg_url = f"https://management.azure.com{nsg_id}"
                self._progress(f"Ensuring network security group: {nsg_name}.")
                self._put_and_wait(
                    resource_url=nsg_url,
                    api_version=NETWORK_API_VERSION,
                    token=token,
                    label="Network security group",
                    body={
                        "location": location,
                        "properties": {
                            "securityRules": [
                                {
                                    "name": "allow-ssh",
                                    "properties": {
                                        "priority": 1000,
                                        "protocol": "Tcp",
                                        "access": "Allow",
                                        "direction": "Inbound",
                                        "sourceAddressPrefix": "*",
                                        "sourcePortRange": "*",
                                        "destinationAddressPrefix": "*",
                                        "destinationPortRange": "22",
                                    },
                                }
                            ]
                        },
                    },
                )

            if create_public_ip:
                pip_url = f"https://management.azure.com{pip_id}"
                self._progress(f"Ensuring public IP: {pip_name}.")
                self._put_and_wait(
                    resource_url=pip_url,
                    api_version=NETWORK_API_VERSION,
                    token=token,
                    label="Public IP",
                    body={
                        "location": location,
                        "sku": {"name": "Standard"},
                        "properties": {
                            "publicIPAllocationMethod": "Static",
                            "publicIPAddressVersion": "IPv4",
                        },
                    },
                )

            nic_ip_config: Dict[str, Any] = {
                "name": "ipconfig1",
                "properties": {
                    "subnet": {"id": subnet_id},
                    "privateIPAllocationMethod": "Dynamic",
                },
            }
            if create_public_ip:
                nic_ip_config["properties"]["publicIPAddress"] = {"id": pip_id}

            nic_props: Dict[str, Any] = {"ipConfigurations": [nic_ip_config]}
            if enable_public_ssh:
                nic_props["networkSecurityGroup"] = {"id": nsg_id}

            nic_url = f"https://management.azure.com{nic_id}"
            self._progress(f"Ensuring network interface: {nic_name}.")
            self._put_and_wait(
                resource_url=nic_url,
                api_version=NETWORK_API_VERSION,
                token=token,
                label="Network interface",
                body={"location": location, "properties": nic_props},
            )

            image = req.image or {}
            image_ref = {
                "publisher": str(image.get("publisher", "")),
                "offer": str(image.get("offer", "")),
                "sku": str(image.get("sku", "")),
                "version": str(image.get("version", "latest")),
            }
            for key, value in image_ref.items():
                if not value:
                    raise ValueError(f"Missing image field: {key}")

            vm_url = f"https://management.azure.com{vm_id}"
            self._progress(
                "Ensuring virtual machine with image "
                f"{image_ref['publisher']}:{image_ref['offer']}:{image_ref['sku']}:{image_ref['version']}."
            )
            tags = {"managed-by": "icopa", **req.tags}
            vm_body = {
                "location": location,
                "tags": tags,
                "properties": {
                    "hardwareProfile": {"vmSize": req.vm_size},
                    "storageProfile": {
                        "imageReference": image_ref,
                        "osDisk": {
                            "createOption": "FromImage",
                            "deleteOption": self._normalize_delete_option(req.os_disk_delete_option),
                        },
                    },
                    "osProfile": {
                        "computerName": req.vm_name[:64],
                        "adminUsername": req.admin_username,
                        "linuxConfiguration": {
                            "disablePasswordAuthentication": True,
                            "ssh": {
                                "publicKeys": [
                                    {
                                        "path": f"/home/{req.admin_username}/.ssh/authorized_keys",
                                        "keyData": req.ssh_public_key,
                                    }
                                ]
                            },
                        },
                    },
                    "networkProfile": {"networkInterfaces": [{"id": nic_id, "properties": {"primary": True}}]},
                },
            }
            if req.spot_enabled:
                vm_body["properties"]["priority"] = "Spot"
                vm_body["properties"]["evictionPolicy"] = self._normalize_eviction_policy(req.spot_eviction_policy)
                if req.spot_max_price is not None:
                    vm_body["properties"]["billingProfile"] = {"maxPrice": req.spot_max_price}
            data_disks = vm_body["properties"]["storageProfile"].get("dataDisks")
            if isinstance(data_disks, list):
                for disk in data_disks:
                    if isinstance(disk, dict):
                        disk["deleteOption"] = self._normalize_delete_option(req.data_disk_delete_option)
            if req.custom_data:
                vm_body["properties"]["osProfile"]["customData"] = req.custom_data

            vm_final = self._put_and_wait(
                resource_url=vm_url,
                api_version=COMPUTE_VM_API_VERSION,
                token=token,
                label="Virtual machine",
                body=vm_body,
            )
            public_ip = self._get_public_ip(token, pip_id) if create_public_ip else ""
            self._progress("Provisioning succeeded.")
            return ProvisionVMResult(
                success=True,
                provider="AZURE",
                status="SUCCEEDED",
                vm_id=str(vm_final.get("id") or vm_id),
                vm_name=req.vm_name,
                public_ip=public_ip,
                resource_group=req.resource_group,
                subscription_id=subscription_id,
                resource_ids={
                    "vm_id": vm_id,
                    "nic_id": nic_id,
                    "public_ip_id": pip_id if create_public_ip else "",
                    "nsg_id": nsg_id if enable_public_ssh else "",
                    "subnet_id": subnet_id,
                },
                details={"location": location, "vm_size": req.vm_size},
            )
        except Exception as exc:
            self._progress(f"Provisioning failed: {exc}")
            return ProvisionVMResult(
                success=False,
                provider="AZURE",
                status="FAILED",
                vm_name=req.vm_name,
                error=str(exc),
            )

    def delete_vm(self, req: DeleteVMRequest) -> DeleteVMResult:
        deleted: Dict[str, bool] = {}
        try:
            token = self._get_access_token()
            vm_id = req.resource_ids.get(
                "vm_id",
                f"/subscriptions/{req.subscription_id}/resourceGroups/{req.resource_group}/providers/"
                f"Microsoft.Compute/virtualMachines/{req.vm_name}",
            )
            vm_url = f"https://management.azure.com{vm_id}"
            self._request_json(
                "DELETE",
                vm_url,
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION},
                allowed_statuses=(200, 202, 204, 404),
            )
            self._wait_until_gone(vm_url, COMPUTE_VM_API_VERSION, token, label="VM")
            deleted["vm"] = True

            if req.delete_attached_resources:
                mapping = {
                    "nic": ("nic_id", NETWORK_API_VERSION),
                    "public_ip": ("public_ip_id", NETWORK_API_VERSION),
                    "nsg": ("nsg_id", NETWORK_API_VERSION),
                }
                for label, (resource_key, api_version) in mapping.items():
                    resource_id = req.resource_ids.get(resource_key, "")
                    if not resource_id:
                        continue
                    resource_url = f"https://management.azure.com{resource_id}"
                    self._request_json(
                        "DELETE",
                        resource_url,
                        token=token,
                        params={"api-version": api_version},
                        allowed_statuses=(200, 202, 204, 404),
                    )
                    self._wait_until_gone(resource_url, api_version, token, label=label)
                    deleted[label] = True

            return DeleteVMResult(
                success=True,
                provider="AZURE",
                status="DELETED",
                vm_name=req.vm_name,
                deleted_resources=deleted,
            )
        except Exception as exc:
            return DeleteVMResult(
                success=False,
                provider="AZURE",
                status="FAILED",
                vm_name=req.vm_name,
                deleted_resources=deleted,
                error=str(exc),
            )

    def stop_vm(self, req: StopVMRequest) -> StopVMResult:
        try:
            self._progress("Requesting Azure access token.")
            token = self._get_access_token()
            subscription_id = str(req.subscription_id or "").strip()
            if not subscription_id:
                subscription_selector = str(req.subscription_selector or "").strip()
                if not subscription_selector:
                    raise ValueError("subscription_selector or subscription_id is required to stop VM.")
                self._progress("Resolving subscription.")
                subscription_id = self._resolve_subscription_id(subscription_selector, token)

            vm_id = str(req.vm_id or "").strip()
            if not vm_id:
                vm_id = (
                    f"/subscriptions/{subscription_id}/resourceGroups/{req.resource_group}/providers/"
                    f"Microsoft.Compute/virtualMachines/{req.vm_name}"
                )
            vm_url = f"https://management.azure.com{vm_id}"
            action = "deallocate" if bool(req.deallocate) else "powerOff"
            self._progress(f"Stopping VM '{req.vm_name}' with action '{action}'.")
            self._request_json(
                "POST",
                f"{vm_url}/{action}",
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION},
                allowed_statuses=(200, 202, 204),
            )
            power_state = self._wait_for_power_state(
                vm_url,
                COMPUTE_VM_API_VERSION,
                token,
                label="Virtual machine",
                target_states=("stopped", "deallocated"),
            )
            vm_detail = self._request_json(
                "GET",
                vm_url,
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION, "$expand": "instanceView"},
                allowed_statuses=(200,),
            ).json()
            vm_props = vm_detail.get("properties", {}) or {}
            vm_hardware = vm_props.get("hardwareProfile", {}) or {}
            final_power_state = self._extract_power_state(vm_detail) or power_state
            self._progress(f"VM stop completed. Power state: '{final_power_state or 'unknown'}'.")
            return StopVMResult(
                success=True,
                provider="AZURE",
                status="STOPPED",
                vm_name=req.vm_name,
                resource_group=req.resource_group,
                subscription_id=subscription_id,
                vm_id=vm_id,
                power_state=final_power_state,
                details={
                    "location": str(vm_detail.get("location", "")),
                    "vm_size": str(vm_hardware.get("vmSize", "")),
                    "provisioning_state": str(vm_props.get("provisioningState", "")),
                },
            )
        except Exception as exc:
            self._progress(f"Stop VM failed: {exc}")
            return StopVMResult(
                success=False,
                provider="AZURE",
                status="FAILED",
                vm_name=req.vm_name,
                resource_group=req.resource_group,
                subscription_id=str(req.subscription_id or ""),
                vm_id=str(req.vm_id or ""),
                error=str(exc),
            )

    def start_vm(self, req: StartVMRequest) -> StartVMResult:
        try:
            self._progress("Requesting Azure access token.")
            token = self._get_access_token()
            subscription_id = str(req.subscription_id or "").strip()
            if not subscription_id:
                subscription_selector = str(req.subscription_selector or "").strip()
                if not subscription_selector:
                    raise ValueError("subscription_selector or subscription_id is required to start VM.")
                self._progress("Resolving subscription.")
                subscription_id = self._resolve_subscription_id(subscription_selector, token)

            vm_id = str(req.vm_id or "").strip()
            if not vm_id:
                vm_id = (
                    f"/subscriptions/{subscription_id}/resourceGroups/{req.resource_group}/providers/"
                    f"Microsoft.Compute/virtualMachines/{req.vm_name}"
                )
            vm_url = f"https://management.azure.com{vm_id}"
            self._progress(f"Starting VM '{req.vm_name}'.")
            self._request_json(
                "POST",
                f"{vm_url}/start",
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION},
                allowed_statuses=(200, 202, 204),
            )
            power_state = self._wait_for_power_state(
                vm_url,
                COMPUTE_VM_API_VERSION,
                token,
                label="Virtual machine",
                target_states=("running",),
            )
            vm_detail = self._request_json(
                "GET",
                vm_url,
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION, "$expand": "instanceView"},
                allowed_statuses=(200,),
            ).json()
            vm_props = vm_detail.get("properties", {}) or {}
            vm_hardware = vm_props.get("hardwareProfile", {}) or {}
            final_power_state = self._extract_power_state(vm_detail) or power_state
            self._progress(f"VM start completed. Power state: '{final_power_state or 'unknown'}'.")
            return StartVMResult(
                success=True,
                provider="AZURE",
                status="STARTED",
                vm_name=req.vm_name,
                resource_group=req.resource_group,
                subscription_id=subscription_id,
                vm_id=vm_id,
                power_state=final_power_state,
                details={
                    "location": str(vm_detail.get("location", "")),
                    "vm_size": str(vm_hardware.get("vmSize", "")),
                    "provisioning_state": str(vm_props.get("provisioningState", "")),
                },
            )
        except Exception as exc:
            self._progress(f"Start VM failed: {exc}")
            return StartVMResult(
                success=False,
                provider="AZURE",
                status="FAILED",
                vm_name=req.vm_name,
                resource_group=req.resource_group,
                subscription_id=str(req.subscription_id or ""),
                vm_id=str(req.vm_id or ""),
                error=str(exc),
            )

    def list_vms_in_resource_group(
        self, *, subscription_selector: str, resource_group: str
    ) -> Dict[str, Any]:
        """List VMs in a given resource group for a selected subscription."""
        try:
            self._progress("Requesting Azure access token.")
            token = self._get_access_token()
            self._progress("Resolving subscription.")
            subscription_id = self._resolve_subscription_id(subscription_selector, token)
            self._progress(f"Listing VMs in resource group: {resource_group}.")
            url = (
                "https://management.azure.com/subscriptions/"
                f"{subscription_id}/resourceGroups/{resource_group}/providers/"
                "Microsoft.Compute/virtualMachines"
            )
            response = self._request_json(
                "GET",
                url,
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION},
                allowed_statuses=(200,),
            )
            values = (response.json() or {}).get("value", [])
            vms: List[Dict[str, Any]] = []
            for vm in values:
                props = vm.get("properties", {}) or {}
                hardware = props.get("hardwareProfile", {}) or {}
                vm_id = str(vm.get("id", ""))
                vm_name = str(vm.get("name", ""))

                vm_detail = self._request_json(
                    "GET",
                    f"https://management.azure.com{vm_id}",
                    token=token,
                    params={"api-version": COMPUTE_VM_API_VERSION, "$expand": "instanceView"},
                    allowed_statuses=(200,),
                ).json()
                power_state = self._extract_power_state(vm_detail)
                network_data = self._collect_vm_network_details(
                    token=token,
                    vm_detail=vm_detail,
                )
                vms.append(
                    {
                        "name": vm_name,
                        "id": vm_id,
                        "location": str(vm.get("location", "")),
                        "vm_size": str(hardware.get("vmSize", "")),
                        "provisioning_state": str(props.get("provisioningState", "")),
                        "power_state": power_state,
                        "network_interfaces": network_data["network_interfaces"],
                        "network_security_groups": network_data["network_security_groups"],
                        "private_ips": network_data["private_ips"],
                        "public_ips": network_data["public_ips"],
                    }
                )
            self._progress(f"Found {len(vms)} VM(s).")
            return {
                "success": True,
                "subscription_id": subscription_id,
                "resource_group": resource_group,
                "vms": vms,
                "error": "",
            }
        except Exception as exc:
            self._progress(f"List VMs failed: {exc}")
            return {
                "success": False,
                "subscription_id": "",
                "resource_group": resource_group,
                "vms": [],
                "error": str(exc),
            }

    def get_vm_instance_view(
        self,
        *,
        subscription_selector: str,
        resource_group: str,
        vm_name: str,
    ) -> Dict[str, Any]:
        """Get one VM with instance view details and resolved network addresses."""
        try:
            self._progress("Requesting Azure access token.")
            token = self._get_access_token()
            self._progress("Resolving subscription.")
            subscription_id = self._resolve_subscription_id(subscription_selector, token)
            vm_url = (
                "https://management.azure.com/subscriptions/"
                f"{subscription_id}/resourceGroups/{resource_group}/providers/"
                f"Microsoft.Compute/virtualMachines/{vm_name}"
            )
            response = self._request_json(
                "GET",
                vm_url,
                token=token,
                params={"api-version": COMPUTE_VM_API_VERSION, "$expand": "instanceView"},
                allowed_statuses=(200, 404),
            )
            if response.status_code == 404:
                return {
                    "success": True,
                    "exists": False,
                    "subscription_id": subscription_id,
                    "resource_group": resource_group,
                    "vm_name": vm_name,
                    "vm": {},
                    "error": "",
                }

            vm_payload = response.json() or {}
            vm_props = vm_payload.get("properties", {}) or {}
            vm_hardware = vm_props.get("hardwareProfile", {}) or {}
            network_data = self._collect_vm_network_details(token=token, vm_detail=vm_payload)
            vm_data = {
                "id": str(vm_payload.get("id", "")),
                "name": str(vm_payload.get("name", vm_name)),
                "location": str(vm_payload.get("location", "")),
                "vm_size": str(vm_hardware.get("vmSize", "")),
                "provisioning_state": str(vm_props.get("provisioningState", "")),
                "power_state": self._extract_power_state(vm_payload),
                "network_interfaces": network_data["network_interfaces"],
                "network_security_groups": network_data["network_security_groups"],
                "private_ips": network_data["private_ips"],
                "public_ips": network_data["public_ips"],
                "tags": vm_payload.get("tags") or {},
                "raw_vm": vm_payload,
            }
            return {
                "success": True,
                "exists": True,
                "subscription_id": subscription_id,
                "resource_group": resource_group,
                "vm_name": vm_name,
                "vm": vm_data,
                "error": "",
            }
        except Exception as exc:
            self._progress(f"Get VM instance view failed: {exc}")
            return {
                "success": False,
                "exists": False,
                "subscription_id": "",
                "resource_group": resource_group,
                "vm_name": vm_name,
                "vm": {},
                "error": str(exc),
            }

    def build_inventory_vm_candidate(
        self,
        *,
        vm_lookup: Dict[str, Any],
        default_vm_name: str,
        default_user_name: str = "ubuntu",
        fallback_address: str = "",
    ) -> Dict[str, Any]:
        """
        Normalize VM lookup payload into an inventory-ready candidate.

        This does not persist data; Hub task code owns DB updates.
        """
        vm_data = vm_lookup.get("vm")
        if not isinstance(vm_data, dict):
            vm_data = {}

        vm_name = str(vm_data.get("name") or vm_lookup.get("vm_name") or default_vm_name).strip()
        public_ips_raw = vm_data.get("public_ips")
        private_ips_raw = vm_data.get("private_ips")
        nics_raw = vm_data.get("network_interfaces")

        public_ips = [str(value).strip() for value in public_ips_raw] if isinstance(public_ips_raw, list) else []
        public_ips = [value for value in public_ips if value]
        private_ips = [str(value).strip() for value in private_ips_raw] if isinstance(private_ips_raw, list) else []
        private_ips = [value for value in private_ips if value]
        network_interfaces = [str(value).strip() for value in nics_raw] if isinstance(nics_raw, list) else []
        network_interfaces = [value for value in network_interfaces if value]

        address = ""
        if public_ips:
            address = public_ips[0]
        elif private_ips:
            address = private_ips[0]
        elif fallback_address.strip():
            address = fallback_address.strip()

        return {
            "can_create_inventory_vm": bool(vm_name and address),
            "vm_name": vm_name,
            "address": address,
            "user_name": str(default_user_name or "ubuntu").strip() or "ubuntu",
            "provider_name": "AZURE",
            "location": str(vm_data.get("location", "")),
            "size": str(vm_data.get("vm_size", "")),
            "power_state": str(vm_data.get("power_state", "")),
            "public_ip": public_ips[0] if public_ips else "",
            "nic_name": network_interfaces[0] if network_interfaces else "",
            "public_ips": public_ips,
            "private_ips": private_ips,
            "network_interfaces": network_interfaces,
        }

    def _get_access_token(self) -> str:
        client_id = self.credential.get("AZURE_CLIENT_ID", "")
        tenant_id = self.credential.get("AZURE_TENANT_ID", "")
        client_secret = self.credential.get("AZURE_CLIENT_SECRET", "")
        if not client_id or not tenant_id or not client_secret:
            raise ValueError("Missing one or more required Azure credentials.")
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        response = requests.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": TOKEN_SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        token = response.json().get("access_token", "")
        if not token:
            raise RuntimeError("No access token returned by Azure AD.")
        return token

    def _resolve_subscription_id(self, selector: str, token: str) -> str:
        response = self._request_json(
            "GET",
            "https://management.azure.com/subscriptions",
            token=token,
            params={"api-version": SUBSCRIPTIONS_API_VERSION},
        )
        values = (response.json() or {}).get("value", [])
        selector_lc = selector.lower()
        for item in values:
            if item.get("subscriptionId") == selector:
                return str(item["subscriptionId"])
        for item in values:
            if str(item.get("displayName", "")).lower() == selector_lc:
                return str(item["subscriptionId"])
        raise ValueError(f"Subscription '{selector}' not found.")

    def _resolve_existing_subnet(
        self,
        *,
        token: str,
        subscription_id: str,
        resource_group: str,
        vnet_name: str,
        subnet_name: str,
    ) -> tuple[str, str]:
        vnet_url = (
            "https://management.azure.com/subscriptions/"
            f"{subscription_id}/resourceGroups/{resource_group}/providers/"
            f"Microsoft.Network/virtualNetworks/{vnet_name}"
        )
        response = self._request_json(
            "GET",
            vnet_url,
            token=token,
            params={"api-version": NETWORK_API_VERSION},
        )
        payload = response.json()
        location = self._normalize_region(str(payload.get("location", "")))
        for subnet in payload.get("properties", {}).get("subnets", []) or []:
            if str(subnet.get("name", "")) == subnet_name:
                subnet_id = str(subnet.get("id", ""))
                if subnet_id:
                    return subnet_id, location
        raise ValueError(f"Subnet '{subnet_name}' not found in VNet '{vnet_name}'.")

    def _check_vm_size_available(
        self,
        token: str,
        subscription_id: str,
        location: str,
        vm_size: str,
    ) -> bool:
        url = (
            "https://management.azure.com/subscriptions/"
            f"{subscription_id}/providers/Microsoft.Compute/skus"
        )
        response = self._request_json(
            "GET",
            url,
            token=token,
            params={"api-version": COMPUTE_SKUS_API_VERSION},
        )
        target = vm_size.lower()
        values = (response.json() or {}).get("value", [])
        for sku in values:
            if sku.get("resourceType") != "virtualMachines":
                continue
            if str(sku.get("name", "")).lower() != target:
                continue
            regions = [self._normalize_region(str(item)) for item in (sku.get("locations") or [])]
            if location not in regions:
                continue
            blocked = False
            for restriction in sku.get("restrictions", []) or []:
                reason = str(restriction.get("reasonCode", "")).lower()
                if reason != "notavailableforsubscription":
                    continue
                blocked_locations = [
                    self._normalize_region(str(item))
                    for item in (restriction.get("restrictionInfo", {}).get("locations") or [])
                ]
                if not blocked_locations or location in blocked_locations:
                    blocked = True
                    break
            if not blocked:
                return True
        return False

    def _put_and_wait(
        self,
        *,
        resource_url: str,
        api_version: str,
        token: str,
        label: str,
        body: dict,
    ) -> dict:
        self._progress(f"{label}: create/update request submitted.")
        self._request_json(
            "PUT",
            resource_url,
            token=token,
            params={"api-version": api_version},
            payload=body,
            allowed_statuses=(200, 201, 202),
        )
        self._wait_for_provisioning(resource_url, api_version, token, label)
        final = self._request_json(
            "GET",
            resource_url,
            token=token,
            params={"api-version": api_version},
            allowed_statuses=(200, 201),
        )
        self._progress(f"{label}: ready.")
        return final.json()

    def _wait_for_provisioning(self, resource_url: str, api_version: str, token: str, label: str) -> None:
        deadline = time.time() + self.max_poll_seconds
        last_state: str = ""
        while time.time() < deadline:
            resp = self._request_json(
                "GET",
                resource_url,
                token=token,
                params={"api-version": api_version},
                allowed_statuses=(200, 201),
            )
            state = str(resp.json().get("properties", {}).get("provisioningState", "")).lower()
            if state in ("", "succeeded"):
                if state == "succeeded":
                    self._progress(f"{label}: provisioning succeeded.")
                return
            if state in ("failed", "canceled"):
                raise RuntimeError(f"{label} provisioning failed with state '{state}'.")
            if state != last_state:
                self._progress(f"{label}: provisioning state '{state}'.")
                last_state = state
            time.sleep(self.poll_interval)
        raise TimeoutError(f"Timed out waiting for {label} provisioning.")

    def _wait_until_gone(self, resource_url: str, api_version: str, token: str, *, label: str) -> None:
        deadline = time.time() + self.max_poll_seconds
        while time.time() < deadline:
            resp = requests.get(
                resource_url,
                headers={"Authorization": f"Bearer {token}"},
                params={"api-version": api_version},
                timeout=self.timeout,
            )
            if resp.status_code == 404:
                return
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"{label} delete poll failed: HTTP {resp.status_code} {resp.text[:200]}")
            time.sleep(self.poll_interval)
        raise TimeoutError(f"Timed out waiting for {label} deletion.")

    def _wait_for_power_state(
        self,
        resource_url: str,
        api_version: str,
        token: str,
        *,
        label: str,
        target_states: tuple[str, ...],
    ) -> str:
        deadline = time.time() + self.max_poll_seconds
        target = {str(item).strip().lower() for item in target_states if str(item).strip()}
        last_state = ""
        while time.time() < deadline:
            resp = self._request_json(
                "GET",
                resource_url,
                token=token,
                params={"api-version": api_version, "$expand": "instanceView"},
                allowed_statuses=(200, 201),
            )
            power_state = self._extract_power_state(resp.json()).strip().lower()
            if power_state in target:
                self._progress(f"{label}: power state '{power_state}'.")
                return power_state
            if power_state != last_state:
                self._progress(f"{label}: waiting for stop, current power state '{power_state or '<unknown>'}'.")
                last_state = power_state
            time.sleep(self.poll_interval)
        raise TimeoutError(f"Timed out waiting for {label} stop state.")

    def _get_public_ip(self, token: str, pip_id: str) -> str:
        response = self._request_json(
            "GET",
            f"https://management.azure.com{pip_id}",
            token=token,
            params={"api-version": NETWORK_API_VERSION},
        )
        return str(response.json().get("properties", {}).get("ipAddress", ""))

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        token: str,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        allowed_statuses: tuple[int, ...] = (200,),
    ) -> requests.Response:
        headers = {"Authorization": f"Bearer {token}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code not in allowed_statuses:
            raise RuntimeError(f"Azure API {method} {url} failed: HTTP {response.status_code} {response.text[:300]}")
        return response

    def _progress(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    @staticmethod
    def _normalize_eviction_policy(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "delete":
            return "Delete"
        return "Deallocate"

    @staticmethod
    def _normalize_delete_option(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "detach":
            return "Detach"
        return "Delete"

    @staticmethod
    def _normalize_region(region_input: str) -> str:
        return "".join(str(region_input).strip().lower().replace("-", " ").replace("_", " ").split())

    def _extract_power_state(self, vm_payload: Dict[str, Any]) -> str:
        statuses = (
            vm_payload.get("properties", {})
            .get("instanceView", {})
            .get("statuses", [])
            or []
        )
        for status in statuses:
            code = str(status.get("code", ""))
            if code.startswith("PowerState/"):
                return code.split("/", 1)[1]
        return ""

    def _collect_vm_network_details(self, *, token: str, vm_detail: Dict[str, Any]) -> Dict[str, List[str]]:
        nic_names: List[str] = []
        nsg_names: set[str] = set()
        private_ips: List[str] = []
        public_ips: List[str] = []
        network_profile = vm_detail.get("properties", {}).get("networkProfile", {}) or {}
        nic_refs = network_profile.get("networkInterfaces", []) or []
        for nic_ref in nic_refs:
            nic_id = str(nic_ref.get("id", "")).strip()
            if not nic_id:
                continue
            nic_names.append(self._resource_name_from_id(nic_id))
            nic = self._request_json(
                "GET",
                f"https://management.azure.com{nic_id}",
                token=token,
                params={"api-version": NETWORK_API_VERSION},
                allowed_statuses=(200,),
            ).json()
            nic_props = nic.get("properties", {}) or {}
            nsg_id = str((nic_props.get("networkSecurityGroup") or {}).get("id", ""))
            if nsg_id:
                nsg_names.add(self._resource_name_from_id(nsg_id))
            for ip_cfg in nic_props.get("ipConfigurations", []) or []:
                ip_props = ip_cfg.get("properties", {}) or {}
                private_ip = str(ip_props.get("privateIPAddress", "")).strip()
                if private_ip:
                    private_ips.append(private_ip)
                pip_id = str((ip_props.get("publicIPAddress") or {}).get("id", "")).strip()
                if pip_id:
                    pip = self._request_json(
                        "GET",
                        f"https://management.azure.com{pip_id}",
                        token=token,
                        params={"api-version": NETWORK_API_VERSION},
                        allowed_statuses=(200,),
                    ).json()
                    ip_address = str(
                        (pip.get("properties", {}) or {}).get("ipAddress", "")
                    ).strip()
                    if ip_address:
                        public_ips.append(ip_address)
        return {
            "network_interfaces": nic_names,
            "network_security_groups": sorted(nsg_names),
            "private_ips": private_ips,
            "public_ips": public_ips,
        }

    @staticmethod
    def _resource_name_from_id(resource_id: str) -> str:
        rid = str(resource_id).rstrip("/")
        if not rid:
            return ""
        return rid.split("/")[-1]

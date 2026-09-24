from __future__ import annotations

import json
import base64
from pathlib import Path
import sys

import pytest
import requests

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from icopa_core.cloud_provisioning.azure import AzureProvisioner
from icopa_core.cloud_provisioning.base import DeleteVMRequest, ProvisionVMRequest, StartVMRequest, StopVMRequest
from icopa_core.cloud_provisioning.azure import build_provision_request_from_vm_config


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def credentials():
    return {
        "AZURE_CLIENT_ID": "cid",
        "AZURE_TENANT_ID": "tid",
        "AZURE_CLIENT_SECRET": "sec",
    }


@pytest.fixture
def provision_request():
    return ProvisionVMRequest(
        subscription_selector="sub-id",
        resource_group="rg-test",
        location="eastus2",
        vm_name="vm-test",
        vm_size="Standard_D2s_v3",
        admin_username="ubuntu",
        ssh_public_key="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local",
        image={
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
        },
        network={"vnet_name": "vnet1", "subnet_name": "default"},
    )


def test_get_access_token_success(monkeypatch, credentials):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: FakeResponse(200, {"access_token": "token-1"}),
    )
    provisioner = AzureProvisioner(credentials)
    assert provisioner._get_access_token() == "token-1"


def test_get_access_token_auth_failure(monkeypatch, credentials):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse(401, {"error": "invalid_client"}))
    provisioner = AzureProvisioner(credentials)
    with pytest.raises(requests.HTTPError):
        provisioner._get_access_token()


def test_vm_size_restriction_returns_false(monkeypatch, credentials):
    def fake_request(*args, **kwargs):
        return FakeResponse(
            200,
            {
                "value": [
                    {
                        "resourceType": "virtualMachines",
                        "name": "Standard_D2s_v3",
                        "locations": ["eastus2"],
                        "restrictions": [
                            {
                                "reasonCode": "NotAvailableForSubscription",
                                "restrictionInfo": {"locations": ["eastus2"]},
                            }
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(requests, "request", fake_request)
    provisioner = AzureProvisioner(credentials)
    assert provisioner._check_vm_size_available("token", "sub-id", "eastus2", "Standard_D2s_v3") is False


def test_provision_vm_success(monkeypatch, credentials, provision_request):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(
        AzureProvisioner,
        "_resolve_existing_subnet",
        lambda self, **kwargs: ("/subscriptions/sub-id/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/default", "eastus2"),
    )
    monkeypatch.setattr(AzureProvisioner, "_check_vm_size_available", lambda self, *args, **kwargs: True)
    monkeypatch.setattr(AzureProvisioner, "_put_and_wait", lambda self, **kwargs: {"id": kwargs["resource_url"].split(".com")[-1]})
    monkeypatch.setattr(AzureProvisioner, "_get_public_ip", lambda self, token, pip_id: "52.1.1.1")

    provisioner = AzureProvisioner(credentials)
    result = provisioner.provision_vm(provision_request)
    assert result.success is True
    assert result.vm_name == "vm-test"
    assert result.public_ip == "52.1.1.1"
    assert result.resource_ids["vm_id"].endswith("/virtualMachines/vm-test")


def test_provision_vm_spot_and_disk_delete_options(monkeypatch, credentials, provision_request):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(
        AzureProvisioner,
        "_resolve_existing_subnet",
        lambda self, **kwargs: (
            "/subscriptions/sub-id/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/default",
            "eastus2",
        ),
    )
    monkeypatch.setattr(AzureProvisioner, "_check_vm_size_available", lambda self, *args, **kwargs: True)
    monkeypatch.setattr(AzureProvisioner, "_get_public_ip", lambda self, token, pip_id: "52.1.1.1")

    captured: dict = {}

    def _capture_put(self, **kwargs):
        if kwargs.get("label") == "Virtual machine":
            captured["body"] = kwargs.get("body")
        return {"id": kwargs["resource_url"].split(".com")[-1]}

    monkeypatch.setattr(AzureProvisioner, "_put_and_wait", _capture_put)
    req = ProvisionVMRequest(
        subscription_selector=provision_request.subscription_selector,
        resource_group=provision_request.resource_group,
        location=provision_request.location,
        vm_name=provision_request.vm_name,
        vm_size=provision_request.vm_size,
        admin_username=provision_request.admin_username,
        ssh_public_key=provision_request.ssh_public_key,
        image=provision_request.image,
        network=provision_request.network,
        custom_data=provision_request.custom_data,
        spot_enabled=True,
        spot_eviction_policy="Deallocate",
        spot_max_price=-1.0,
        os_disk_delete_option="Delete",
    )
    result = AzureProvisioner(credentials).provision_vm(req)
    assert result.success is True
    vm_body = captured.get("body") or {}
    props = vm_body.get("properties") or {}
    assert props.get("priority") == "Spot"
    assert props.get("evictionPolicy") == "Deallocate"
    assert (props.get("billingProfile") or {}).get("maxPrice") == -1.0
    assert ((props.get("storageProfile") or {}).get("osDisk") or {}).get("createOption") == "FromImage"
    assert ((props.get("storageProfile") or {}).get("osDisk") or {}).get("deleteOption") == "Delete"


def test_provision_vm_quota_unavailable(monkeypatch, credentials, provision_request):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(
        AzureProvisioner,
        "_resolve_existing_subnet",
        lambda self, **kwargs: ("/subscriptions/sub-id/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/default", "eastus2"),
    )
    monkeypatch.setattr(AzureProvisioner, "_check_vm_size_available", lambda self, *args, **kwargs: False)
    result = AzureProvisioner(credentials).provision_vm(provision_request)
    assert result.success is False
    assert "not available" in result.error.lower()


def test_provision_vm_timeout(monkeypatch, credentials, provision_request):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(
        AzureProvisioner,
        "_resolve_existing_subnet",
        lambda self, **kwargs: ("/subscriptions/sub-id/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/default", "eastus2"),
    )
    monkeypatch.setattr(AzureProvisioner, "_check_vm_size_available", lambda self, *args, **kwargs: True)

    def _timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(AzureProvisioner, "_put_and_wait", _timeout)
    result = AzureProvisioner(credentials).provision_vm(provision_request)
    assert result.success is False
    assert "timed out" in result.error


def test_delete_vm_cleanup_failure(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(
        requests,
        "request",
        lambda *args, **kwargs: FakeResponse(202, {}),
    )

    def fake_get(*args, **kwargs):
        return FakeResponse(500, {}, "boom")

    monkeypatch.setattr(requests, "get", fake_get)
    result = AzureProvisioner(credentials).delete_vm(
        DeleteVMRequest(
            subscription_id="sub-id",
            resource_group="rg1",
            vm_name="vm1",
            resource_ids={"vm_id": "/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1"},
        )
    )
    assert result.success is False
    assert "delete poll failed" in result.error


def test_stop_vm_success(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(AzureProvisioner, "_wait_for_power_state", lambda self, *args, **kwargs: "deallocated")

    def fake_request_json(self, method, url, **kwargs):
        if method == "POST" and url.endswith("/deallocate"):
            return FakeResponse(202, {})
        if method == "GET" and "/virtualMachines/vm-test" in url:
            return FakeResponse(
                200,
                {
                    "id": "/subscriptions/sub-id/resourceGroups/rg-test/providers/Microsoft.Compute/virtualMachines/vm-test",
                    "name": "vm-test",
                    "location": "eastus2",
                    "properties": {
                        "provisioningState": "Succeeded",
                        "hardwareProfile": {"vmSize": "Standard_D2s_v3"},
                        "instanceView": {"statuses": [{"code": "PowerState/deallocated"}]},
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {method} {url}")

    monkeypatch.setattr(AzureProvisioner, "_request_json", fake_request_json)
    result = AzureProvisioner(credentials).stop_vm(
        StopVMRequest(
            subscription_selector="sub-id",
            resource_group="rg-test",
            vm_name="vm-test",
        )
    )
    assert result.success is True
    assert result.status == "STOPPED"
    assert result.power_state == "deallocated"
    assert result.subscription_id == "sub-id"


def test_stop_vm_failure(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")

    def _raise_request(*args, **kwargs):
        raise RuntimeError("not found")

    monkeypatch.setattr(AzureProvisioner, "_request_json", _raise_request)
    result = AzureProvisioner(credentials).stop_vm(
        StopVMRequest(
            subscription_selector="sub-id",
            resource_group="rg-test",
            vm_name="vm-test",
        )
    )
    assert result.success is False
    assert "not found" in result.error


def test_start_vm_success(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(AzureProvisioner, "_wait_for_power_state", lambda self, *args, **kwargs: "running")

    def fake_request_json(self, method, url, **kwargs):
        if method == "POST" and url.endswith("/start"):
            return FakeResponse(202, {})
        if method == "GET" and "/virtualMachines/vm-test" in url:
            return FakeResponse(
                200,
                {
                    "id": "/subscriptions/sub-id/resourceGroups/rg-test/providers/Microsoft.Compute/virtualMachines/vm-test",
                    "name": "vm-test",
                    "location": "eastus2",
                    "properties": {
                        "provisioningState": "Succeeded",
                        "hardwareProfile": {"vmSize": "Standard_D2s_v3"},
                        "instanceView": {"statuses": [{"code": "PowerState/running"}]},
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {method} {url}")

    monkeypatch.setattr(AzureProvisioner, "_request_json", fake_request_json)
    result = AzureProvisioner(credentials).start_vm(
        StartVMRequest(
            subscription_selector="sub-id",
            resource_group="rg-test",
            vm_name="vm-test",
        )
    )
    assert result.success is True
    assert result.status == "STARTED"
    assert result.power_state == "running"
    assert result.subscription_id == "sub-id"


def test_start_vm_failure(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")

    def _raise_request(*args, **kwargs):
        raise RuntimeError("conflict")

    monkeypatch.setattr(AzureProvisioner, "_request_json", _raise_request)
    result = AzureProvisioner(credentials).start_vm(
        StartVMRequest(
            subscription_selector="sub-id",
            resource_group="rg-test",
            vm_name="vm-test",
        )
    )
    assert result.success is False
    assert "conflict" in result.error


def test_get_vm_instance_view_success(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")

    def fake_request_json(self, method, url, **kwargs):
        if "/virtualMachines/vm-test" in url:
            return FakeResponse(
                200,
                {
                    "id": "/subscriptions/sub-id/resourceGroups/rg-test/providers/Microsoft.Compute/virtualMachines/vm-test",
                    "name": "vm-test",
                    "location": "eastus2",
                    "properties": {
                        "provisioningState": "Succeeded",
                        "hardwareProfile": {"vmSize": "Standard_D2s_v3"},
                        "instanceView": {"statuses": [{"code": "PowerState/running"}]},
                        "networkProfile": {
                            "networkInterfaces": [
                                {
                                    "id": "/subscriptions/sub-id/resourceGroups/rg-test/providers/Microsoft.Network/networkInterfaces/vm-test-nic"
                                }
                            ]
                        },
                    },
                },
            )
        if "/networkInterfaces/vm-test-nic" in url:
            return FakeResponse(
                200,
                {
                    "properties": {
                        "networkSecurityGroup": {
                            "id": "/subscriptions/sub-id/resourceGroups/rg-test/providers/Microsoft.Network/networkSecurityGroups/vm-test-nsg"
                        },
                        "ipConfigurations": [
                            {
                                "properties": {
                                    "privateIPAddress": "10.0.0.4",
                                    "publicIPAddress": {
                                        "id": "/subscriptions/sub-id/resourceGroups/rg-test/providers/Microsoft.Network/publicIPAddresses/vm-test-pip"
                                    },
                                }
                            }
                        ],
                    }
                },
            )
        if "/publicIPAddresses/vm-test-pip" in url:
            return FakeResponse(200, {"properties": {"ipAddress": "52.1.1.1"}})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(AzureProvisioner, "_request_json", fake_request_json)
    result = AzureProvisioner(credentials).get_vm_instance_view(
        subscription_selector="sub-id",
        resource_group="rg-test",
        vm_name="vm-test",
    )
    assert result["success"] is True
    assert result["exists"] is True
    assert result["subscription_id"] == "sub-id"
    assert result["vm"]["power_state"] == "running"
    assert result["vm"]["public_ips"] == ["52.1.1.1"]


def test_get_vm_instance_view_not_found(monkeypatch, credentials):
    monkeypatch.setattr(AzureProvisioner, "_get_access_token", lambda self: "token")
    monkeypatch.setattr(AzureProvisioner, "_resolve_subscription_id", lambda self, selector, token: "sub-id")
    monkeypatch.setattr(
        AzureProvisioner,
        "_request_json",
        lambda self, method, url, **kwargs: FakeResponse(404, {}),
    )
    result = AzureProvisioner(credentials).get_vm_instance_view(
        subscription_selector="sub-id",
        resource_group="rg-test",
        vm_name="vm-missing",
    )
    assert result["success"] is True
    assert result["exists"] is False
    assert result["vm_name"] == "vm-missing"


def test_build_inventory_vm_candidate_prefers_public_ip(credentials):
    provisioner = AzureProvisioner(credentials)
    candidate = provisioner.build_inventory_vm_candidate(
        vm_lookup={
            "exists": True,
            "vm_name": "vm-a",
            "vm": {
                "name": "vm-a",
                "location": "eastus2",
                "vm_size": "Standard_D2s_v3",
                "power_state": "running",
                "public_ips": ["52.1.1.1"],
                "private_ips": ["10.0.0.4"],
                "network_interfaces": ["nic-a"],
            },
        },
        default_vm_name="fallback-vm",
        default_user_name="ubuntu",
        fallback_address="",
    )
    assert candidate["can_create_inventory_vm"] is True
    assert candidate["vm_name"] == "vm-a"
    assert candidate["address"] == "52.1.1.1"
    assert candidate["public_ip"] == "52.1.1.1"
    assert candidate["nic_name"] == "nic-a"


def test_build_inventory_vm_candidate_uses_fallback_address(credentials):
    provisioner = AzureProvisioner(credentials)
    candidate = provisioner.build_inventory_vm_candidate(
        vm_lookup={"exists": True, "vm_name": "vm-b", "vm": {"public_ips": [], "private_ips": []}},
        default_vm_name="vm-b",
        default_user_name="ubuntu",
        fallback_address="203.0.113.10",
    )
    assert candidate["can_create_inventory_vm"] is True
    assert candidate["address"] == "203.0.113.10"


def test_build_provision_request_from_vm_config_builds_cloud_init_when_post_setup_set():
    req = build_provision_request_from_vm_config(
        {
            "subscription_id": "sub-id",
            "resource_group": "rg1",
            "location": "eastus2",
            "name": "vm-cloud-init",
            "size": "Standard_D2s_v3",
            "admin_username": "ubuntu",
            "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local",
            "image": {
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
            "network": {"vnet_name": "vnet1", "subnet_name": "default"},
            "post_setup": {"install_docker": True},
        }
    )
    assert req.custom_data
    decoded = base64.b64decode(req.custom_data).decode("utf-8")
    assert "#cloud-config" in decoded
    assert "docker.io" in decoded


def test_build_provision_request_from_vm_config_supports_spot_and_storage():
    req = build_provision_request_from_vm_config(
        {
            "subscription_id": "sub-id",
            "resource_group": "rg1",
            "location": "eastus2",
            "name": "vm-spot",
            "size": "Standard_D2s_v3",
            "admin_username": "ubuntu",
            "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local",
            "image": {
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
            "network": {"vnet_name": "vnet1", "subnet_name": "default"},
            "spot": {"enabled": True, "eviction_policy": "Deallocate", "max_price": -1},
            "storage": {"os_disk_delete_option": "Delete", "data_disk_delete_option": "Delete"},
        }
    )
    assert req.spot_enabled is True
    assert req.spot_eviction_policy == "Deallocate"
    assert req.spot_max_price == -1.0
    assert req.os_disk_delete_option == "Delete"
    assert req.data_disk_delete_option == "Delete"

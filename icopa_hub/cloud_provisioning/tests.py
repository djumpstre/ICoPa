from __future__ import annotations

import os
from dataclasses import dataclass, field
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SSHCredential, VM

from .celery_tasks import (
    check_provisioning_vm_task,
    deprovision_vm_task,
    provision_vm_task,
    start_provisioning_vm_task,
    stop_provisioning_vm_task,
)
from .models import ProvisioningVM, ProvisioningVMCheckRequest, ProvisioningVMStartRequest, ProvisioningVMStopRequest


User = get_user_model()


@dataclass
class ProvisionVMResult:
    success: bool
    provider: str
    status: str
    vm_id: str = ""
    vm_name: str = ""
    public_ip: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    resource_ids: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class DeleteVMResult:
    success: bool
    provider: str
    status: str
    vm_name: str = ""
    deleted_resources: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class StopVMResult:
    success: bool
    provider: str
    status: str
    vm_name: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    vm_id: str = ""
    power_state: str = ""
    details: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class StartVMResult:
    success: bool
    provider: str
    status: str
    vm_name: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    vm_id: str = ""
    power_state: str = ""
    details: dict = field(default_factory=dict)
    error: str = ""


class CloudProvisioningAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent", password="testpass123")
        self.other_user = User.objects.create_user(username="other", password="testpass123")
        self.client.force_authenticate(user=self.user)
        self.ssh_credential = SSHCredential.objects.create(
            name="key1",
            key_path="/tmp/id_rsa",
            user_name="ubuntu",
            created_by=self.user,
        )

    def _request_spec(self) -> dict:
        return {
            "subscription_selector": "sub-1",
            "resource_group": "rg-test",
            "group_name": "cloud-group-a",
            "location": "eastus2",
            "vm_size": "Standard_D2s_v3",
            "admin_username": "ubuntu",
            "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local",
            "image": {
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts-gen2",
                "version": "latest",
            },
            "network": {"vnet_name": "vnet1", "subnet_name": "default"},
            "icopa_config": {
                "networking": {
                    "public": True,
                    "openPorts": [
                        {"proto": "tcp", "port": 22, "purpose": "ssh"},
                        {"proto": "tcp", "port": 9328, "purpose": "zenoh-router"},
                    ],
                }
            },
        }

    def _upload_template(self, content: bytes):
        upload = SimpleUploadedFile("az_template.yaml", content, content_type="application/x-yaml")
        response = self.client.post(
            reverse("cloud_vm_upload"),
            data={"file": upload},
            format="multipart",
        )
        return response

    def test_upload_creates_initialized_vms_with_raw_template(self):
        response = self._upload_template(
            b"""
kind: VMProvisioning
metadata:
  group_name: upload-group
  cloud_provider: azure
vms:
  - name: vm-1
    subscription_id: sub-1
    resource_group: rg-1
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: key1
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
    icopa_config:
      networking:
        public: true
        openPorts:
          - { proto: tcp, port: 22, purpose: ssh }
  - name: vm-2
    subscription_id: sub-2
    resource_group: rg-2
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: key1
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
"""
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["total_created"], 2)
        vm = ProvisioningVM.objects.get(created_by=self.user, name="vm-1")
        self.assertEqual(vm.status, ProvisioningVM.Status.INITIALIZED)
        self.assertEqual(vm.group_name, "upload-group")
        self.assertEqual(vm.ssh_credential_id, self.ssh_credential.id)
        self.assertEqual(vm.raw_template.get("kind"), "VMProvisioning")
        self.assertEqual(vm.request_spec.get("group_name"), "upload-group")
        self.assertEqual(vm.icopa_config.get("networking", {}).get("public"), True)

    def test_upload_rejects_non_azure_provider(self):
        response = self._upload_template(
            b"""
kind: VMProvisioning
metadata:
  cloud_provider: aws
vms:
  - name: vm-1
    subscription_id: sub-1
    resource_group: rg-1
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: key1
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
"""
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("support soon", str(response.json()))

    def test_upload_rejects_duplicate_vm_names_in_same_template(self):
        response = self._upload_template(
            b"""
kind: VMProvisioning
metadata:
  group_name: dup-group
  cloud_provider: azure
vms:
  - name: vm-dup
    subscription_id: sub-1
    resource_group: rg-1
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: key1
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
  - name: vm-dup
    subscription_id: sub-1
    resource_group: rg-1
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: key1
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
"""
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Duplicate vm names", str(response.json()))
        self.assertIn("dup-group", str(response.json()))

    def test_upload_rejects_existing_vm_name(self):
        ProvisioningVM.objects.create(
            name="vm-existing",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.INITIALIZED,
            request_spec=self._request_spec(),
            created_by=self.user,
            modified_by=self.user,
        )
        response = self._upload_template(
            b"""
kind: VMProvisioning
metadata:
  group_name: existing-group
  cloud_provider: azure
vms:
  - name: vm-existing
    subscription_id: sub-1
    resource_group: rg-1
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: key1
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
"""
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already exist", str(response.json()))
        self.assertIn("existing-group", str(response.json()))

    def test_upload_rejects_unknown_ssh_private_key_name(self):
        response = self._upload_template(
            b"""
kind: VMProvisioning
metadata:
  group_name: ssh-group
  cloud_provider: azure
vms:
  - name: vm-ssh-missing
    subscription_id: sub-1
    resource_group: rg-1
    location: eastus2
    size: Standard_D2s_v3
    admin_username: ubuntu
    ssh_private_key_name_in_icopa: not-existing-key
    ssh_public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local
    image:
      publisher: Canonical
      offer: 0001-com-ubuntu-server-jammy
      sku: 22_04-lts-gen2
"""
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unknown SSH key", str(response.json()))

    @patch("cloud_provisioning.api.provision_vm_task.delay")
    def test_create_vm_endpoint_starts_from_initialized(self, delay_mock):
        delay_mock.return_value = Mock(id="task-123")
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-init",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.INITIALIZED,
            request_spec=self._request_spec(),
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        response = self.client.post(
            reverse("cloud_vm_create", kwargs={"pk": provisioning_vm.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.PENDING)
        self.assertEqual(provisioning_vm.task_id, "task-123")

    @patch("cloud_provisioning.api.deprovision_vm_task.delay")
    def test_delete_endpoint_accepted(self, delay_mock):
        delay_mock.return_value = Mock(id="task-delete")
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-delete",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            ssh_credential=self.ssh_credential,
            result_data={"subscription_id": "sub-1", "resource_group": "rg-test"},
            created_by=self.user,
            modified_by=self.user,
        )
        response = self.client.post(
            reverse("cloud_vm_delete", kwargs={"pk": provisioning_vm.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.DELETING)
        self.assertEqual(provisioning_vm.task_id, "task-delete")

    def test_delete_initialized_is_directly_deleted_without_provider_arg(self):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-delete-2",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.INITIALIZED,
            request_spec=self._request_spec(),
            created_by=self.user,
            modified_by=self.user,
        )
        response = self.client.post(
            reverse("cloud_vm_delete", kwargs={"pk": provisioning_vm.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.DELETED)

    @patch("cloud_provisioning.api.check_provisioning_vm_task.delay")
    def test_check_endpoint_creates_check_request(self, delay_mock):
        delay_mock.return_value = Mock(id="task-check")
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-check",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            created_by=self.user,
            modified_by=self.user,
        )
        response = self.client.post(
            reverse("cloud_vm_check", kwargs={"pk": provisioning_vm.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            ProvisioningVMCheckRequest.objects.filter(provisioning_vm=provisioning_vm).count(),
            1,
        )

    @patch("cloud_provisioning.api.stop_provisioning_vm_task.delay")
    def test_stop_endpoint_creates_stop_request(self, delay_mock):
        delay_mock.return_value = Mock(id="task-stop")
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-stop",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            result_data={"subscription_id": "sub-id", "resource_group": "rg1", "vm_name": "vm-auto"},
            created_by=self.user,
            modified_by=self.user,
        )
        response = self.client.post(
            reverse("cloud_vm_stop", kwargs={"pk": provisioning_vm.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            ProvisioningVMStopRequest.objects.filter(provisioning_vm=provisioning_vm).count(),
            1,
        )

    @patch("cloud_provisioning.api.start_provisioning_vm_task.delay")
    def test_start_endpoint_creates_start_request(self, delay_mock):
        delay_mock.return_value = Mock(id="task-start")
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-start",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            result_data={"subscription_id": "sub-id", "resource_group": "rg1", "vm_name": "vm-auto"},
            created_by=self.user,
            modified_by=self.user,
        )
        response = self.client.post(
            reverse("cloud_vm_start", kwargs={"pk": provisioning_vm.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            ProvisioningVMStartRequest.objects.filter(provisioning_vm=provisioning_vm).count(),
            1,
        )

    def test_stop_request_detail_endpoint_returns_payload(self):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-stop-detail",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            created_by=self.user,
            modified_by=self.user,
        )
        stop_request = ProvisioningVMStopRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMStopRequest.Status.PASS,
            task_id="task-stop-detail",
            stop_data={"stop_result": {"status": "STOPPED"}},
            created_by=self.user,
            modified_by=self.user,
        )

        response = self.client.get(
            reverse("cloud_vm_stop_detail", kwargs={"pk": provisioning_vm.id, "stop_id": stop_request.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["id"], stop_request.id)
        self.assertEqual(payload["provisioning_vm_id"], provisioning_vm.id)
        self.assertEqual(payload["status"], ProvisioningVMStopRequest.Status.PASS)

    def test_start_request_detail_endpoint_returns_payload(self):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-start-detail",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            created_by=self.user,
            modified_by=self.user,
        )
        start_request = ProvisioningVMStartRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMStartRequest.Status.PASS,
            task_id="task-start-detail",
            start_data={"start_result": {"status": "STARTED"}},
            created_by=self.user,
            modified_by=self.user,
        )

        response = self.client.get(
            reverse("cloud_vm_start_detail", kwargs={"pk": provisioning_vm.id, "start_id": start_request.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["id"], start_request.id)
        self.assertEqual(payload["provisioning_vm_id"], provisioning_vm.id)
        self.assertEqual(payload["status"], ProvisioningVMStartRequest.Status.PASS)

    def test_check_request_detail_endpoint_returns_payload(self):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-check-detail",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self._request_spec(),
            created_by=self.user,
            modified_by=self.user,
        )
        check_request = ProvisioningVMCheckRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMCheckRequest.Status.PASS,
            task_id="task-check-detail",
            check_data={"cloud_vm_exists": True, "vm_name": "vm-check-detail"},
            created_by=self.user,
            modified_by=self.user,
        )

        response = self.client.get(
            reverse("cloud_vm_check_detail", kwargs={"pk": provisioning_vm.id, "check_id": check_request.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["id"], check_request.id)
        self.assertEqual(payload["provisioning_vm_id"], provisioning_vm.id)
        self.assertEqual(payload["status"], ProvisioningVMCheckRequest.Status.PASS)
        self.assertEqual(payload["provisioning_vm_status"], ProvisioningVM.Status.SUCCEEDED)

    def test_user_cannot_access_another_users_vm(self):
        provisioning_vm = ProvisioningVM.objects.create(
            name="other-vm",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.PENDING,
            request_spec=self._request_spec(),
            created_by=self.other_user,
            modified_by=self.other_user,
        )
        response = self.client.get(reverse("cloud_vm_detail", kwargs={"pk": provisioning_vm.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CloudProvisioningTaskTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent", password="testpass123")
        self.ssh_credential = SSHCredential.objects.create(
            name="key1",
            key_path="/tmp/id_rsa",
            user_name="ubuntu",
            created_by=self.user,
        )
        self.request_spec = {
            "subscription_selector": "sub-id",
            "resource_group": "rg1",
            "group_name": "cloud-group-a",
            "location": "eastus2",
            "vm_name": "vm-auto",
            "vm_size": "Standard_D2s_v3",
            "admin_username": "ubuntu",
            "ssh_private_key_name_in_icopa": "key1",
            "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCy test@local",
            "image": {"publisher": "Canonical", "offer": "ubuntu", "sku": "22_04", "version": "latest"},
            "network": {"vnet_name": "vnet1", "subnet_name": "default"},
            "icopa_config": {
                "networking": {
                    "public": True,
                    "openPorts": [
                        {"proto": "tcp", "port": 22, "purpose": "ssh"},
                        {"proto": "tcp", "port": 9328, "purpose": "zenoh-router"},
                    ],
                }
            },
        }

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_provision_task_success_upserts_inventory_vm(self, provisioner_factory):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-success",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.PENDING,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.provision_vm.return_value = ProvisionVMResult(
            success=True,
            provider="AZURE",
            status="SUCCEEDED",
            vm_id="/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto",
            vm_name="vm-auto",
            public_ip="52.1.1.1",
            resource_group="rg1",
            subscription_id="sub-id",
            resource_ids={"vm_id": "/vm", "nic_id": "/nic", "public_ip_id": "/pip", "nsg_id": "/nsg"},
        )
        provisioner_factory.return_value = provisioner

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = provision_vm_task.run(provisioning_vm_id=provisioning_vm.id, user_id=self.user.id)

        self.assertTrue(result["ok"])
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.SUCCEEDED)
        self.assertEqual(provisioning_vm.instance_provider_name, "AZURE")
        self.assertEqual(provisioning_vm.instance_location, "eastus2")
        self.assertEqual(provisioning_vm.instance_size, "Standard_D2s_v3")
        self.assertEqual(provisioning_vm.instance_public_ip, "52.1.1.1")
        vm = VM.objects.get(created_by=self.user, name="vm-auto")
        self.assertEqual(vm.group_name, "cloud-group-a")
        self.assertEqual(vm.address, "52.1.1.1")
        self.assertEqual(vm.status, VM.VMStatus.ACTIVE)
        self.assertFalse(vm.archived)
        self.assertTrue(vm.auto_provisioned)
        self.assertEqual(vm.cloud_provisioning_vm_id, provisioning_vm.id)
        self.assertEqual(vm.credential_id, self.ssh_credential.id)
        self.assertEqual(vm.metadata.get("ssh_private_key_name_in_icopa"), "key1")
        self.assertEqual(vm.metadata.get("icopa_config", {}).get("networking", {}).get("public"), True)
        self.assertEqual(vm.networking.get("public"), True)

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_provision_task_uses_raw_template_post_setup_for_custom_data(self, provisioner_factory):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-with-cloud-init",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.PENDING,
            request_spec=self.request_spec,
            raw_template={
                "metadata": {"cloud_provider": "azure"},
                "vms": [
                    {
                        "name": "vm-auto",
                        "post_setup": {"install_docker": True},
                        "container": {
                            "name": "ros2-runtime",
                            "image": "example.invalid/ros2:latest",
                            "run_args": "--network host --restart unless-stopped",
                        },
                    }
                ],
            },
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()

        def _fake_provision(req):
            self.assertTrue(req.custom_data)
            return ProvisionVMResult(
                success=True,
                provider="AZURE",
                status="SUCCEEDED",
                vm_id="/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto",
                vm_name="vm-auto",
                public_ip="52.1.1.9",
                resource_group="rg1",
                subscription_id="sub-id",
                resource_ids={"vm_id": "/vm", "nic_id": "/nic"},
                details={"location": "eastus2", "vm_size": "Standard_D2s_v3"},
            )

        provisioner.provision_vm.side_effect = _fake_provision
        provisioner_factory.return_value = provisioner

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = provision_vm_task.run(provisioning_vm_id=provisioning_vm.id, user_id=self.user.id)

        self.assertTrue(result["ok"])
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.SUCCEEDED)

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_provision_task_failure_updates_status(self, provisioner_factory):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-failed",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.PENDING,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.provision_vm.return_value = ProvisionVMResult(
            success=False,
            provider="AZURE",
            status="FAILED",
            vm_name="vm-auto",
            error="sku unavailable",
        )
        provisioner_factory.return_value = provisioner

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = provision_vm_task.run(provisioning_vm_id=provisioning_vm.id, user_id=self.user.id)

        self.assertFalse(result["ok"])
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.FAILED)
        self.assertIn("sku unavailable", provisioning_vm.last_error)

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_deprovision_task_archives_inventory_vm(self, provisioner_factory):
        vm = VM.objects.create(
            name="vm-auto",
            address="52.1.1.1",
            user_name="ubuntu",
            created_by=self.user,
            modified_by=self.user,
            status=VM.VMStatus.ACTIVE,
            credential=self.ssh_credential,
        )
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-delete",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            inventory_vm=vm,
            result_data={
                "subscription_id": "sub-id",
                "resource_group": "rg1",
                "resource_ids": {"vm_id": "/vm", "nic_id": "/nic", "public_ip_id": "/pip", "nsg_id": "/nsg"},
                "vm_name": "vm-auto",
            },
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.delete_vm.return_value = DeleteVMResult(
            success=True,
            provider="AZURE",
            status="DELETED",
            vm_name="vm-auto",
            deleted_resources={"vm": True},
        )
        provisioner_factory.return_value = provisioner

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = deprovision_vm_task.run(provisioning_vm_id=provisioning_vm.id, user_id=self.user.id)

        self.assertTrue(result["ok"])
        provisioning_vm.refresh_from_db()
        vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.DELETED)
        self.assertEqual(vm.status, VM.VMStatus.INACTIVE)
        self.assertTrue(vm.archived)

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_stop_task_updates_instance_snapshot_and_inventory_status(self, provisioner_factory):
        vm = VM.objects.create(
            name="vm-auto",
            address="52.1.1.1",
            user_name="ubuntu",
            created_by=self.user,
            modified_by=self.user,
            status=VM.VMStatus.ACTIVE,
            credential=self.ssh_credential,
        )
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-stop-task",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            inventory_vm=vm,
            result_data={
                "subscription_id": "sub-id",
                "resource_group": "rg1",
                "vm_name": "vm-auto",
                "resource_ids": {"vm_id": "/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto"},
            },
            created_by=self.user,
            modified_by=self.user,
        )
        stop_request = ProvisioningVMStopRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMStopRequest.Status.PENDING,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.stop_vm.return_value = StopVMResult(
            success=True,
            provider="AZURE",
            status="STOPPED",
            vm_name="vm-auto",
            resource_group="rg1",
            subscription_id="sub-id",
            vm_id="/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto",
            power_state="deallocated",
            details={"location": "eastus2", "vm_size": "Standard_D2s_v3"},
        )
        provisioner.get_vm_instance_view.return_value = {
            "success": True,
            "exists": True,
            "subscription_id": "sub-id",
            "resource_group": "rg1",
            "vm_name": "vm-auto",
            "vm": {
                "name": "vm-auto",
                "location": "eastus2",
                "vm_size": "Standard_D2s_v3",
                "power_state": "deallocated",
                "public_ips": ["52.1.1.1"],
                "network_interfaces": ["vm-auto-nic"],
            },
            "error": "",
        }
        provisioner_factory.return_value = provisioner

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = stop_provisioning_vm_task.run(
                stop_request_id=stop_request.id,
                provisioning_vm_id=provisioning_vm.id,
                user_id=self.user.id,
            )

        self.assertTrue(result["ok"])
        stop_request.refresh_from_db()
        provisioning_vm.refresh_from_db()
        vm.refresh_from_db()
        self.assertEqual(stop_request.status, ProvisioningVMStopRequest.Status.PASS)
        self.assertEqual(provisioning_vm.instance_power_state, "deallocated")
        self.assertEqual(vm.status, VM.VMStatus.INACTIVE)
        self.assertEqual(vm.metadata.get("cloud_last_action"), "stop_vm")

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_start_task_updates_instance_snapshot_and_inventory_status(self, provisioner_factory):
        vm = VM.objects.create(
            name="vm-auto",
            address="52.1.1.1",
            user_name="ubuntu",
            created_by=self.user,
            modified_by=self.user,
            status=VM.VMStatus.INACTIVE,
            credential=self.ssh_credential,
        )
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-start-task",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            inventory_vm=vm,
            result_data={
                "subscription_id": "sub-id",
                "resource_group": "rg1",
                "vm_name": "vm-auto",
                "resource_ids": {"vm_id": "/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto"},
            },
            created_by=self.user,
            modified_by=self.user,
        )
        start_request = ProvisioningVMStartRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMStartRequest.Status.PENDING,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.start_vm.return_value = StartVMResult(
            success=True,
            provider="AZURE",
            status="STARTED",
            vm_name="vm-auto",
            resource_group="rg1",
            subscription_id="sub-id",
            vm_id="/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto",
            power_state="running",
            details={"location": "eastus2", "vm_size": "Standard_D2s_v3"},
        )
        provisioner.get_vm_instance_view.return_value = {
            "success": True,
            "exists": True,
            "subscription_id": "sub-id",
            "resource_group": "rg1",
            "vm_name": "vm-auto",
            "vm": {
                "name": "vm-auto",
                "location": "eastus2",
                "vm_size": "Standard_D2s_v3",
                "power_state": "running",
                "public_ips": ["52.1.1.1"],
                "network_interfaces": ["vm-auto-nic"],
            },
            "error": "",
        }
        provisioner_factory.return_value = provisioner

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = start_provisioning_vm_task.run(
                start_request_id=start_request.id,
                provisioning_vm_id=provisioning_vm.id,
                user_id=self.user.id,
            )

        self.assertTrue(result["ok"])
        start_request.refresh_from_db()
        provisioning_vm.refresh_from_db()
        vm.refresh_from_db()
        self.assertEqual(start_request.status, ProvisioningVMStartRequest.Status.PASS)
        self.assertEqual(provisioning_vm.instance_power_state, "running")
        self.assertEqual(vm.status, VM.VMStatus.ACTIVE)
        self.assertEqual(vm.metadata.get("cloud_last_action"), "start_vm")

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_check_task_passes_when_env_is_ready(self, provisioner_factory):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-check",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.INITIALIZED,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.get_vm_instance_view.return_value = {
            "success": True,
            "exists": True,
            "subscription_id": "sub-id",
            "resource_group": "rg1",
            "vm_name": "vm-auto",
            "vm": {
                "id": "/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto",
                "name": "vm-auto",
                "location": "eastus2",
                "vm_size": "Standard_D2s_v3",
                "provisioning_state": "Succeeded",
                "power_state": "running",
                "public_ips": ["52.1.1.1"],
                "private_ips": ["10.0.0.4"],
                "network_interfaces": ["vm-auto-nic"],
                "network_security_groups": ["vm-auto-nsg"],
            },
            "error": "",
        }
        provisioner.build_inventory_vm_candidate.return_value = {
            "can_create_inventory_vm": True,
            "vm_name": "vm-auto",
            "address": "52.1.1.1",
            "user_name": "ubuntu",
            "provider_name": "AZURE",
            "location": "eastus2",
            "size": "Standard_D2s_v3",
            "power_state": "running",
            "public_ip": "52.1.1.1",
            "nic_name": "vm-auto-nic",
            "public_ips": ["52.1.1.1"],
            "private_ips": ["10.0.0.4"],
            "network_interfaces": ["vm-auto-nic"],
        }
        provisioner_factory.return_value = provisioner
        check_request = ProvisioningVMCheckRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMCheckRequest.Status.PENDING,
            created_by=self.user,
            modified_by=self.user,
        )

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = check_provisioning_vm_task.run(
                check_request_id=check_request.id,
                provisioning_vm_id=provisioning_vm.id,
                user_id=self.user.id,
            )

        self.assertTrue(result["ok"])
        check_request.refresh_from_db()
        self.assertEqual(check_request.status, ProvisioningVMCheckRequest.Status.PASS)
        self.assertTrue(check_request.check_data.get("cloud_vm_exists"))
        self.assertEqual(check_request.check_data.get("power_state"), "running")
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.SUCCEEDED)
        self.assertEqual(provisioning_vm.result_data.get("subscription_id"), "sub-id")
        self.assertEqual(provisioning_vm.instance_provider_name, "AZURE")
        self.assertEqual(provisioning_vm.instance_location, "eastus2")
        self.assertEqual(provisioning_vm.instance_size, "Standard_D2s_v3")
        self.assertEqual(provisioning_vm.instance_power_state, "running")
        self.assertEqual(provisioning_vm.instance_public_ip, "52.1.1.1")
        self.assertEqual(provisioning_vm.instance_nic_name, "vm-auto-nic")
        self.assertIsNotNone(provisioning_vm.inventory_vm_id)
        inventory_vm = VM.objects.get(id=provisioning_vm.inventory_vm_id)
        self.assertEqual(inventory_vm.group_name, "cloud-group-a")
        self.assertEqual(inventory_vm.address, "52.1.1.1")
        self.assertTrue(inventory_vm.auto_provisioned)
        self.assertEqual(inventory_vm.cloud_provisioning_vm_id, provisioning_vm.id)
        self.assertEqual(inventory_vm.networking.get("public"), True)
        self.assertEqual(inventory_vm.credential_id, self.ssh_credential.id)
        self.assertEqual(inventory_vm.metadata.get("ssh_private_key_name_in_icopa"), "key1")

    def test_inventory_vm_is_deleted_when_provisioning_vm_deleted(self):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-cascade",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.SUCCEEDED,
            request_spec=self.request_spec,
            icopa_config=self.request_spec.get("icopa_config") or {},
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        vm = VM.objects.create(
            name="vm-auto",
            group_name="cloud-group-a",
            address="52.1.1.1",
            user_name="ubuntu",
            status=VM.VMStatus.ACTIVE,
            auto_provisioned=True,
            cloud_provisioning_vm=provisioning_vm,
            created_by=self.user,
            modified_by=self.user,
            credential=self.ssh_credential,
        )
        provisioning_vm.inventory_vm = vm
        provisioning_vm.save(update_fields=["inventory_vm", "updated_at"])

        provisioning_vm.delete()
        self.assertFalse(VM.objects.filter(id=vm.id).exists())

    @patch("cloud_provisioning.celery_tasks.get_provisioner")
    def test_check_task_fails_when_provisioning_state_not_succeeded(self, provisioner_factory):
        provisioning_vm = ProvisioningVM.objects.create(
            name="vm-check-not-ready",
            provider=ProvisioningVM.Provider.AZURE,
            status=ProvisioningVM.Status.INITIALIZED,
            request_spec=self.request_spec,
            ssh_credential=self.ssh_credential,
            created_by=self.user,
            modified_by=self.user,
        )
        provisioner = Mock()
        provisioner.get_vm_instance_view.return_value = {
            "success": True,
            "exists": True,
            "subscription_id": "sub-id",
            "resource_group": "rg1",
            "vm_name": "vm-auto",
            "vm": {
                "id": "/subscriptions/sub-id/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm-auto",
                "name": "vm-auto",
                "location": "eastus2",
                "vm_size": "Standard_D2s_v3",
                "provisioning_state": "Creating",
                "power_state": "starting",
                "public_ips": ["52.1.1.1"],
                "private_ips": ["10.0.0.4"],
                "network_interfaces": ["vm-auto-nic"],
                "network_security_groups": ["vm-auto-nsg"],
            },
            "error": "",
        }
        provisioner.build_inventory_vm_candidate.return_value = {
            "can_create_inventory_vm": True,
            "vm_name": "vm-auto",
            "address": "52.1.1.1",
            "user_name": "ubuntu",
            "provider_name": "AZURE",
            "location": "eastus2",
            "size": "Standard_D2s_v3",
            "power_state": "starting",
            "public_ip": "52.1.1.1",
            "nic_name": "vm-auto-nic",
            "public_ips": ["52.1.1.1"],
            "private_ips": ["10.0.0.4"],
            "network_interfaces": ["vm-auto-nic"],
        }
        provisioner_factory.return_value = provisioner
        check_request = ProvisioningVMCheckRequest.objects.create(
            provisioning_vm=provisioning_vm,
            status=ProvisioningVMCheckRequest.Status.PENDING,
            created_by=self.user,
            modified_by=self.user,
        )

        with patch.dict(
            os.environ,
            {
                "AZURE_CLIENT_ID": "cid",
                "AZURE_TENANT_ID": "tid",
                "AZURE_CLIENT_SECRET": "sec",
            },
            clear=False,
        ):
            result = check_provisioning_vm_task.run(
                check_request_id=check_request.id,
                provisioning_vm_id=provisioning_vm.id,
                user_id=self.user.id,
            )

        self.assertFalse(result["ok"])
        check_request.refresh_from_db()
        self.assertEqual(check_request.status, ProvisioningVMCheckRequest.Status.FAIL)
        self.assertIn("expected 'Succeeded'", check_request.last_error)
        provisioning_vm.refresh_from_db()
        self.assertEqual(provisioning_vm.status, ProvisioningVM.Status.INITIALIZED)
        self.assertIsNone(provisioning_vm.inventory_vm_id)

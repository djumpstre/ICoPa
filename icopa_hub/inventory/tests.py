from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import VM


User = get_user_model()


class InventoryAPITestCase(APITestCase):
    def setUp(self):
        private = TemporaryDirectory()
        self.addCleanup(private.cleanup)
        isolated = override_settings(PRIVATE_STORAGE_ROOT=Path(private.name))
        isolated.enable()
        self.addCleanup(isolated.disable)
        self.user = User.objects.create_user(username="agent", password="testpass123")
        self.client.force_authenticate(user=self.user)

    def test_uploaded_credentials_have_no_public_url(self):
        from django.core.files.base import ContentFile
        from .models import SSHCredential
        from .serializers import SSHCredentialSerializer

        credential = SSHCredential.objects.create(name="private-key", created_by=self.user, key_path="")
        credential.key_file.save("test.pem", ContentFile(b"test-only"))
        path = Path(credential.key_file.path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn("inventory/ssh_keys/", credential.key_file.name)
        self.assertIsNone(credential.key_file.url)
        self.assertEqual(SSHCredentialSerializer(credential).data["key_file"], credential.key_file.name)

    def test_upload_inventory_creates_vms(self):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")

        response = self.client.post(
            reverse("inventory_list_upload"),
            data={"file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(VM.objects.filter(created_by=self.user).count(), 1)
        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertEqual(vm.address, "10.0.0.1")
        self.assertEqual(vm.user_name, "ubuntu")
        self.assertEqual(vm.group_name, "default")

    def test_upload_inventory_sets_group_name_from_metadata(self):
        content = b"""
metadata:
  group_name: edge-cluster-a
spec:
  hosts:
    - name: vm1
      address: 10.0.0.1
      username: ubuntu
      credential:
        ssh_key_name: key1
  ssh_key:
    - name: key1
      key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        response = self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertEqual(vm.group_name, "edge-cluster-a")

    def test_inventory_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse("inventory_list_upload"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inventory_accepts_bearer_token(self):
        self.client.force_authenticate(user=None)
        login_response = self.client.post(
            reverse("user_login"),
            data={"username": "agent", "password": "testpass123"},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        access_token = login_response.json()["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        response = self.client.get(reverse("inventory_list_upload"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("inventory.api.VMSSHClient")
    def test_check_vm_success(self, ssh_client_cls):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        mock_result = type("VMSSHResult", (), {
            "success": True,
            "returncode": 0,
            "stdout": "icopa_ssh_ok",
            "stderr": "",
        })()
        ssh_client_cls.return_value.test_connectivity.return_value = mock_result
        ssh_client_cls.return_value.check_docker_runtime.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "docker_ok", "stderr": ""},
        )()
        ssh_client_cls.return_value.list_running_containers.return_value = []
        ssh_client_cls.return_value.execute_command.side_effect = [
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "8\n", "stderr": ""})(),
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "16777216\n", "stderr": ""})(),
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "", "stderr": ""})(),
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "00:02.0 VGA compatible controller: Fake GPU\n", "stderr": ""})(),
        ]

        response = self.client.post(reverse("inventory_check_vm"), data={"name": "vm1"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["system_info"]["cpu_cores"], 8)
        self.assertEqual(response.json()["system_info"]["mem_total_gb"], 16.0)
        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertEqual(vm.system_info.get("cpu_cores"), 8)
        self.assertEqual(vm.system_info.get("mem_total_gb"), 16.0)
        self.assertEqual(
            ssh_client_cls.call_args.kwargs["key_path"],
            vm.credential.key_path,
        )

    @patch("inventory.api.VMSSHClient")
    def test_check_vm_failure_returns_200_with_ok_false(self, ssh_client_cls):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        mock_result = type(
            "VMSSHResult",
            (),
            {
                "success": False,
                "returncode": 255,
                "stdout": "",
                "stderr": "Connection timed out",
            },
        )()
        ssh_client_cls.return_value.test_connectivity.return_value = mock_result

        response = self.client.post(reverse("inventory_check_vm"), data={"name": "vm1"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.json()["ok"])

    @patch("inventory.api.VMSSHClient")
    def test_check_vm_ssh_ok_docker_not_ready_is_reported_and_saved(self, ssh_client_cls):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        ssh_client = ssh_client_cls.return_value
        ssh_client.test_connectivity.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "icopa_ssh_ok", "stderr": ""},
        )()
        ssh_client.check_docker_runtime.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "docker_error", "stderr": ""},
        )()
        ssh_client.execute_command.side_effect = [
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "8\n", "stderr": ""})(),
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "16777216\n", "stderr": ""})(),
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "", "stderr": ""})(),
            type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "", "stderr": ""})(),
        ]

        response = self.client.post(reverse("inventory_check_vm"), data={"name": "vm1"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["ssh_ok"])
        self.assertFalse(body["docker_ok"])
        self.assertIn("Docker runtime check failed", body["stderr"])

        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertEqual(vm.status, VM.VMStatus.ACTIVE)
        self.assertEqual(vm.container_runtime_type, "docker")
        self.assertFalse(vm.container_runtime_ready)
        self.assertEqual((vm.metadata or {}).get("connectivity", {}).get("ssh_ok"), True)
        self.assertEqual((vm.metadata or {}).get("connectivity", {}).get("docker_ok"), False)

    def test_delete_vm(self):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        response = self.client.delete(reverse("inventory_vm_detail", kwargs={"vm_name": "vm1"}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(VM.objects.filter(created_by=self.user, name="vm1").exists())

    def test_get_vm_detail_by_name(self):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        response = self.client.get(reverse("inventory_vm_detail", kwargs={"vm_name": "vm1"}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["name"], "vm1")
        self.assertEqual(response.json()["user_name"], "ubuntu")

    def test_upload_existing_host_and_key_returns_updated_fields(self):
        first_content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        second_content = b"""
hosts:
  - name: vm1
    address: 10.0.0.2
    username: ec2-user
    port: 2222
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa_new
"""
        self.client.post(
            reverse("inventory_list_upload"),
            data={"file": SimpleUploadedFile("vm1.yaml", first_content, content_type="application/x-yaml")},
            format="multipart",
        )

        response = self.client.post(
            reverse("inventory_list_upload"),
            data={"file": SimpleUploadedFile("vm2.yaml", second_content, content_type="application/x-yaml")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["updated"]["hosts"][0]["name"], "vm1")
        self.assertEqual(body["updated"]["ssh_key"][0]["name"], "key1")
        self.assertEqual(
            body["updated"]["hosts"][0]["updated_fields"]["address"],
            {"old": "10.0.0.1", "new": "10.0.0.2"},
        )
        self.assertEqual(
            body["updated"]["hosts"][0]["updated_fields"]["user_name"],
            {"old": "ubuntu", "new": "ec2-user"},
        )

    def test_upload_spec_wrapped_inventory_persists_networking(self):
        content = b"""
kind: InventorySpec
metadata:
  name: demo
spec:
  hosts:
    - name: vm1
      address: 10.0.0.1
      username: ubuntu
      port: 22
      credential:
        ssh_key_name: key1
      networking:
        public: true
        openPorts:
          - proto: tcp
            port: 9099
            purpose: zenoh-router
  ssh_key:
    - name: key1
      key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")

        response = self.client.post(
            reverse("inventory_list_upload"),
            data={"file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertEqual(vm.networking.get("public"), True)
        self.assertEqual(vm.networking.get("openPorts")[0]["port"], 9099)

    def test_upload_accepts_dns_address(self):
        content = b"""
spec:
  hosts:
    - name: local_vm
      address: local-vm.example.internal
      username: ubuntu
      credential:
        ssh_key_name: key1
  ssh_key:
    - name: key1
      key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        response = self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        vm = VM.objects.get(created_by=self.user, name="local_vm")
        self.assertEqual(vm.address, "local-vm.example.internal")

    @patch("inventory.api.VMSSHClient")
    def test_upload_with_secret_key_file_uses_stored_file_for_check(self, ssh_client_cls):
        content = b"""
spec:
  hosts:
    - name: vm1
      address: 10.0.0.1
      username: ubuntu
      credential:
        ssh_key_name: key1
  ssh_key:
    - name: key1
      key_path: /workspace/secret_credentials/id_vm_access.pem
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        key_upload = SimpleUploadedFile("id_vm_access.pem", b"dummy-private-key", content_type="application/octet-stream")
        response = self.client.post(
            reverse("inventory_list_upload"),
            data={"file": upload, "ssh_key_file__key1": key_upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertTrue(bool(vm.credential.key_file))

        mock_result = type("VMSSHResult", (), {"success": True, "returncode": 0, "stdout": "ok", "stderr": ""})()
        ssh_client_cls.return_value.test_connectivity.return_value = mock_result
        ssh_client_cls.return_value.check_docker_runtime.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "docker_ok", "stderr": ""},
        )()

        self.client.post(reverse("inventory_check_vm"), data={"name": "vm1"}, format="json")
        self.assertEqual(
            ssh_client_cls.call_args.kwargs["key_path"],
            vm.credential.key_file.path,
        )

    @patch("inventory.api.VMSSHClient")
    def test_check_vm_reports_managed_container_status(self, ssh_client_cls):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")
        vm = VM.objects.get(created_by=self.user, name="vm1")
        vm.managed_containers = ["router-a", "sender-b", "removed-c"]
        vm.save(update_fields=["managed_containers", "updated_at"])

        mock_result = type(
            "VMSSHResult",
            (),
            {
                "success": True,
                "returncode": 0,
                "stdout": "icopa_ssh_ok",
                "stderr": "",
            },
        )()
        ssh_client = ssh_client_cls.return_value
        ssh_client.test_connectivity.return_value = mock_result
        ssh_client.check_docker_runtime.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "docker_ok", "stderr": ""},
        )()
        ssh_client.list_running_containers.return_value = [
            {
                "name": "router-a",
                "id": "abc123",
                "image": "eclipse/zenoh:latest",
                "command": "/bin/router",
                "created_at": "2026-02-19 12:00:00 +0000 UTC",
                "running_for": "5 minutes",
                "ports": "7447/tcp",
                "status": "Up 5 minutes",
            }
        ]
        result_cls = type("VMSSHResult", (), {})

        def _result(success=True, stdout="", stderr="", returncode=0):
            item = result_cls()
            item.success = success
            item.stdout = stdout
            item.stderr = stderr
            item.returncode = returncode
            return item

        def _execute_side_effect(command: str, timeout: int = 20):
            if command == "nproc":
                return _result(stdout="8\n")
            if "MemTotal" in command:
                return _result(stdout="16777216\n")
            if "nvidia-smi" in command:
                return _result(stdout="")
            if "lspci" in command:
                return _result(stdout="")
            if "docker ps --filter \"name=^/sender-b$\"" in command:
                return _result(stdout="")
            if "docker ps -a --filter \"name=^/sender-b$\"" in command:
                return _result(stdout="sender-b|Exited (0) 1 minute ago\n")
            if "docker ps --filter \"name=^/removed-c$\"" in command:
                return _result(stdout="")
            if "docker ps -a --filter \"name=^/removed-c$\"" in command:
                return _result(stdout="")
            return _result(stdout="")

        ssh_client.execute_command.side_effect = _execute_side_effect

        response = self.client.post(reverse("inventory_check_vm"), data={"name": "vm1"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["managed_containers"], ["router-a"])
        self.assertEqual(body["started_containers"], ["router-a"])
        states = {item["name"]: item["state"] for item in body["managed_container_status"]}
        self.assertEqual(states["router-a"], "running")
        self.assertEqual(states["sender-b"], "stopped")
        self.assertEqual(states["removed-c"], "removed")
        router = next(item for item in body["managed_container_status"] if item["name"] == "router-a")
        self.assertEqual(router["id"], "abc123")
        self.assertEqual(router["image"], "eclipse/zenoh:latest")
        self.assertEqual(router["ports"], "7447/tcp")
        self.assertEqual(router["running_for"], "5 minutes")

        vm.refresh_from_db()
        self.assertEqual(vm.managed_containers, ["router-a"])
        self.assertEqual((vm.metadata or {}).get("managed_containers_running"), ["router-a"])

    @patch("inventory.api.VMSSHClient")
    def test_check_vm_discovers_running_icopa_containers_when_tracked_is_empty(self, ssh_client_cls):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        mock_result = type(
            "VMSSHResult",
            (),
            {
                "success": True,
                "returncode": 0,
                "stdout": "icopa_ssh_ok",
                "stderr": "",
            },
        )()
        ssh_client = ssh_client_cls.return_value
        ssh_client.test_connectivity.return_value = mock_result
        ssh_client.check_docker_runtime.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "docker_ok", "stderr": ""},
        )()
        ssh_client.list_running_containers.return_value = [
            {
                "name": "icopa-stress-demo",
                "id": "def456",
                "image": "example.invalid/icopa/icopa-stress-container:v0.1",
                "command": "/bin/stress",
                "created_at": "2026-02-19 12:01:00 +0000 UTC",
                "running_for": "2 minutes",
                "ports": "",
                "status": "Up 2 minutes",
            }
        ]

        result_cls = type("VMSSHResult", (), {})

        def _result(success=True, stdout="", stderr="", returncode=0):
            item = result_cls()
            item.success = success
            item.stdout = stdout
            item.stderr = stderr
            item.returncode = returncode
            return item

        def _execute_side_effect(command: str, timeout: int = 20):
            if command == "nproc":
                return _result(stdout="8\n")
            if "MemTotal" in command:
                return _result(stdout="16777216\n")
            if "nvidia-smi" in command:
                return _result(stdout="")
            if "lspci" in command:
                return _result(stdout="")
            return _result(stdout="")

        ssh_client.execute_command.side_effect = _execute_side_effect

        response = self.client.post(reverse("inventory_check_vm"), data={"name": "vm1"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["managed_containers"], ["icopa-stress-demo"])
        self.assertEqual(body["started_containers"], ["icopa-stress-demo"])

        vm = VM.objects.get(created_by=self.user, name="vm1")
        self.assertEqual(vm.managed_containers, ["icopa-stress-demo"])

    @patch("inventory.api.stop_and_remove_vm_container_task.apply_async")
    @patch("inventory.celery_tasks.VMSSHClient")
    def test_stop_container_by_vm_name(self, ssh_client_cls, apply_async_mock):
        apply_async_mock.side_effect = RuntimeError("broker unavailable")
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")
        vm = VM.objects.get(created_by=self.user, name="vm1")
        vm.managed_containers = ["icopa-zenoh-probe-responder"]
        vm.save(update_fields=["managed_containers", "updated_at"])

        ssh_client = ssh_client_cls.return_value
        ssh_client.test_connectivity.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "ok", "stderr": ""},
        )()
        ssh_client.stop_and_remove_container.return_value = type(
            "VMContainerOperationResult",
            (),
            {
                "ok": True,
                "state": "removed",
                "message": "Container removed.",
                "error": "",
                "stop_stdout": "icopa-zenoh-probe-responder\n",
                "stop_stderr": "",
                "remove_stdout": "icopa-zenoh-probe-responder\n",
                "remove_stderr": "",
            },
        )()
        ssh_client.get_container_status.return_value = type(
            "VMContainerStatus",
            (),
            {
                "name": "icopa-zenoh-probe-responder",
                "exists": False,
                "running": False,
                "status": "",
            },
        )()

        response = self.client.post(
            reverse("inventory_stop_container"),
            data={"vm": "vm1", "container": "icopa-zenoh-probe-responder"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "removed")

        vm.refresh_from_db()
        self.assertEqual(vm.managed_containers, [])

    @patch("inventory.api.stop_and_remove_vm_container_task.AsyncResult")
    @patch("inventory.api.stop_and_remove_vm_container_task.apply_async")
    def test_stop_container_returns_running_while_task_in_progress(self, apply_async_mock, async_result_mock):
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")

        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-stop-1"})()

        first = self.client.post(
            reverse("inventory_stop_container"),
            data={"vm": "vm1", "container": "icopa-zenoh-probe-responder"},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        first_body = first.json()
        self.assertEqual(first_body["status"], "RUNNING")
        self.assertEqual(first_body["task_id"], "task-stop-1")

        async_result_mock.return_value = type("TaskState", (), {"state": "STARTED", "result": None})()
        second = self.client.post(
            reverse("inventory_stop_container"),
            data={"vm": "vm1", "container": "icopa-zenoh-probe-responder", "task_id": "task-stop-1"},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        second_body = second.json()
        self.assertEqual(second_body["status"], "RUNNING")
        self.assertEqual(second_body["task_id"], "task-stop-1")

    @patch("inventory.api.stop_and_remove_vm_container_task.apply_async")
    @patch("inventory.celery_tasks.VMSSHClient")
    def test_stop_container_by_vm_id(self, ssh_client_cls, apply_async_mock):
        apply_async_mock.side_effect = RuntimeError("broker unavailable")
        content = b"""
hosts:
  - name: vm1
    address: 10.0.0.1
    username: ubuntu
    port: 22
    credential:
      ssh_key_name: key1
ssh_key:
  - name: key1
    key_path: /tmp/id_rsa
"""
        upload = SimpleUploadedFile("vm.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("inventory_list_upload"), data={"file": upload}, format="multipart")
        vm = VM.objects.get(created_by=self.user, name="vm1")
        vm.managed_containers = ["icopa-zenoh-probe-responder"]
        vm.save(update_fields=["managed_containers", "updated_at"])

        ssh_client = ssh_client_cls.return_value
        ssh_client.test_connectivity.return_value = type(
            "VMSSHResult",
            (),
            {"success": True, "returncode": 0, "stdout": "ok", "stderr": ""},
        )()
        ssh_client.stop_and_remove_container.return_value = type(
            "VMContainerOperationResult",
            (),
            {
                "ok": True,
                "state": "removed",
                "message": "Container removed.",
                "error": "",
                "stop_stdout": "",
                "stop_stderr": "",
                "remove_stdout": "icopa-zenoh-probe-responder\n",
                "remove_stderr": "",
            },
        )()
        ssh_client.get_container_status.return_value = type(
            "VMContainerStatus",
            (),
            {
                "name": "icopa-zenoh-probe-responder",
                "exists": False,
                "running": False,
                "status": "",
            },
        )()

        response = self.client.post(
            reverse("inventory_stop_container"),
            data={"vm_id": vm.id, "container": "icopa-zenoh-probe-responder"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "removed")

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import RuntimeEnvironment


User = get_user_model()


class RuntimeEnvironmentAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent", password="testpass123")
        self.client.force_authenticate(user=self.user)

    def test_upload_runtime_env_creates_entry(self):
        content = b"""
kind: RuntimeEnvironment
metadata:
  name: ros2-jazzy-zenoh
  tags:
    ros2:
      distro: jazzy
spec:
  images:
    runnerImage: example.invalid/icopa/zenoh_jazzy_ci:v1
  parameters:
    pub_rate_hz: 100.0
  commandPresets:
    - group: testing
      name: sender
      executor: auto
      command: echo sender
"""
        upload = SimpleUploadedFile("runtime.yaml", content, content_type="application/x-yaml")

        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        env = RuntimeEnvironment.objects.get(created_by=self.user, name="ros2-jazzy-zenoh")
        self.assertEqual(env.kind, "RuntimeEnvironment")
        self.assertEqual(env.images["runnerImage"], "example.invalid/icopa/zenoh_jazzy_ci:v1")
        self.assertEqual(env.tags.get("ros2", {}).get("distro"), "jazzy")
        self.assertEqual(env.command_preset[0]["name"], "sender")
        self.assertIn("kind: RuntimeEnvironment", env.raw_yaml)
        self.assertTrue(bool(env.cached_yaml_file))
        self.assertEqual(env.uploaded_version, 1)
        self.assertTrue(str(env.cached_yaml_file.name).startswith("static/runtime/ros2-jazzy-zenoh/1/runtime_env"))

    def test_runtime_env_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse("runtime_env_list_upload"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_runtime_env_detail_and_delete(self):
        content = b"""
metadata:
  name: demo-env
spec:
  images: {}
  tags: {}
  parameters: {}
  commandPresets: []
"""
        upload = SimpleUploadedFile("runtime.yaml", content, content_type="application/x-yaml")
        self.client.post(reverse("runtime_env_list_upload"), data={"file": upload}, format="multipart")

        detail_resp = self.client.get(reverse("runtime_env_detail", kwargs={"env_name": "demo-env"}))
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.json()["name"], "demo-env")

        yaml_resp = self.client.get(reverse("runtime_env_yaml", kwargs={"env_name": "demo-env"}))
        self.assertEqual(yaml_resp.status_code, status.HTTP_200_OK)
        self.assertIn("metadata:", yaml_resp.content.decode("utf-8"))
        self.assertIn("name: demo-env", yaml_resp.content.decode("utf-8"))

        delete_resp = self.client.delete(reverse("runtime_env_detail", kwargs={"env_name": "demo-env"}))
        self.assertEqual(delete_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(RuntimeEnvironment.objects.filter(created_by=self.user, name="demo-env").exists())

    def test_upload_existing_runtime_env_returns_updated_fields(self):
        first_content = b"""
metadata:
  name: demo-env
spec:
  images:
    runnerImage: image:v1
  tags: {}
  parameters: {}
  commandPresets:
    - group: testing
      name: one
      executor: auto
      command: echo one
"""
        second_content = b"""
metadata:
  name: demo-env
spec:
  images:
    runnerImage: image:v2
  tags:
    ros2:
      distro: jazzy
  parameters:
    pub_rate_hz: 10
  commandPresets:
    - group: testing
      name: two
      executor: auto
      command: echo two
"""

        self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": SimpleUploadedFile("first.yaml", first_content, content_type="application/x-yaml")},
            format="multipart",
        )
        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": SimpleUploadedFile("second.yaml", second_content, content_type="application/x-yaml")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["updated"]["runtime_env"][0]["name"], "demo-env")
        self.assertEqual(
            body["updated"]["runtime_env"][0]["updated_fields"]["images"],
            {"old": {"runnerImage": "image:v1"}, "new": {"runnerImage": "image:v2"}},
        )
        env = RuntimeEnvironment.objects.get(created_by=self.user, name="demo-env")
        self.assertEqual(env.uploaded_version, 2)
        self.assertTrue(str(env.cached_yaml_file.name).startswith("static/runtime/demo-env/2/runtime_env"))

    def test_upload_runtime_env_with_command_groups(self):
        content = b"""
kind: RuntimeEnvironment
metadata:
  name: grouped-env
  description: grouped commands
spec:
  images:
    runnerImage: image:v2
  tags: {}
  parameters: {}
  commandGroups:
    routing:
      - name: cloud_router
        executor: ssh
        command: echo route
    testing:
      - name: sender
        executor: auto
        command: echo send
"""
        upload = SimpleUploadedFile("runtime.yaml", content, content_type="application/x-yaml")
        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": upload},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not supported", str(response.json()))

    def test_upload_runtime_env_with_rrt_config_file(self):
        runtime_content = b"""
kind: RuntimeEnvironment
metadata:
  name: zenoh-unit8-vm
spec:
  images:
    runnerImage: image:v3
  tags: {}
  parameters:
    rrt_config_file_path: configs/runtime_env/serializer_rrt_config.yaml
  commandPresets: []
"""
        rrt_content = b"""
icopa_sender:
  ros__parameters:
    payload_size: 1024KB
"""
        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={
                "file": SimpleUploadedFile("zenoh-unit8-vm.yaml", runtime_content, content_type="application/x-yaml"),
                "rrt_config_file": SimpleUploadedFile(
                    "serializer_rrt_config.yaml",
                    rrt_content,
                    content_type="application/x-yaml",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        env = RuntimeEnvironment.objects.get(created_by=self.user, name="zenoh-unit8-vm")
        self.assertTrue(bool(env.serializer_rrt_config_file))
        self.assertIn("payload_size", env.serializer_rrt_config_raw_yaml)
        self.assertTrue(
            env.serializer_rrt_config_path.startswith("static/runtime/zenoh-unit8-vm/1/serializer_rrt_config")
        )

    def test_upload_zenoh_runtime_rejects_profiling_command_groups_alias(self):
        content = b"""
kind: RuntimeEnvironment
metadata:
  name: zenoh-profiled
  tags:
    zenoh:
      enabled: true
spec:
  images:
    runnerImage: image:v3
  parameters: {}
  profilingCommandGroups:
    - name: latency_responder
      executor: auto
      command: echo responder
    - name: latency_sender
      executor: auto
      command: echo sender
"""
        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": SimpleUploadedFile("zenoh-profiled.yaml", content, content_type="application/x-yaml")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not supported", str(response.json()))

    def test_upload_zenoh_runtime_rejects_routing_command_groups(self):
        content = b"""
kind: RuntimeEnvironment
metadata:
  name: zenoh-invalid-routing
  tags:
    zenoh:
      enabled: true
spec:
  images:
    runnerImage: image:v3
  parameters: {}
  commandGroups:
    routing:
      - name: cloud_router
        executor: ssh
        command: echo route
  profilingCommandGroups:
    - name: latency_responder
      executor: auto
      command: echo responder
    - name: latency_sender
      executor: auto
      command: echo sender
"""
        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": SimpleUploadedFile("zenoh-invalid-routing.yaml", content, content_type="application/x-yaml")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not supported", str(response.json()))

    def test_upload_zenoh_runtime_requires_both_latency_presets(self):
        content = b"""
kind: RuntimeEnvironment
metadata:
  name: zenoh-missing-probe
  tags:
    zenoh:
      enabled: true
spec:
  images:
    runnerImage: image:v3
  parameters: {}
  commandPresets:
    - group: profiling
      name: latency_sender
      executor: auto
      command: echo sender
"""
        response = self.client.post(
            reverse("runtime_env_list_upload"),
            data={"file": SimpleUploadedFile("zenoh-missing-probe.yaml", content, content_type="application/x-yaml")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("requires both profiling presets", str(response.json()))

    def test_runtime_action_catalog_endpoint(self):
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm",
            kind="RuntimeEnvironment",
            metadata={"name": "zenoh-unit8-vm"},
            tags={"zenoh": {"enabled": True}},
            command_preset=[
                {"group": "profiling", "name": "latency_responder", "executor": "auto", "command": "echo responder"},
                {"group": "profiling", "name": "latency_sender", "executor": "auto", "command": "echo sender"},
            ],
            command_groups={"profiling": []},
        )
        response = self.client.get(reverse("runtime_env_actions"), data={"env_name": "zenoh-unit8-vm"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertTrue(isinstance(payload.get("canonical_actions"), list))
        self.assertTrue(any(item.get("type") == "run_runtime_preset" for item in payload["canonical_actions"]))
        self.assertTrue(
            any(item.get("type") == "run_runtime_preset" and item.get("scope") == "vm"
                for item in payload["canonical_actions"])
        )
        self.assertEqual(payload.get("runtime_env"), "zenoh-unit8-vm")
        self.assertTrue(isinstance(payload.get("runtime_presets"), list))

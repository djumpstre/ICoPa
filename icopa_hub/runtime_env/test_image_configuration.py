from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase
import yaml

from .models import RuntimeEnvironment


class RuntimeImageConfigurationTests(APITestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(override_settings(BASE_DIR=Path(temporary.name), MEDIA_ROOT=temporary.name))
        self.user = get_user_model().objects.create_user(username="image-contract")
        self.client.force_authenticate(user=self.user)

    def upload(self, images):
        payload = {
            "kind": "RuntimeEnvironment", "metadata": {"name": "user-workload"},
            "spec": {"images": images, "commandPresets": []},
        }
        file = SimpleUploadedFile("runtime.yaml", yaml.safe_dump(payload).encode(), content_type="application/x-yaml")
        return self.client.post(reverse("runtime_env_list_upload"), {"file": file}, format="multipart")

    def test_unconfigured_images_are_rejected_without_saving(self):
        for image in ["", "   ", None, 123]:
            with self.subTest(image=image):
                response = self.upload({"runnerImage": image})
                self.assertEqual(response.status_code, 400)
                self.assertIn("your container image reference", str(response.json()))
        self.assertFalse(RuntimeEnvironment.objects.exists())

    def test_user_supplied_image_is_stored(self):
        response = self.upload({"runnerImage": "example.invalid/team/workload:v1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RuntimeEnvironment.objects.get().images["runnerImage"], "example.invalid/team/workload:v1")

    def test_native_runtime_can_omit_image(self):
        self.assertEqual(self.upload({}).status_code, 200)

    def test_action_catalog_exposes_unconfigured_stress_image(self):
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get(reverse("runtime_env_actions"))
        self.assertEqual(response.status_code, 200)
        self.assertIn('"image_configured":false', response.content.decode().replace(" ", ""))

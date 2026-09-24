from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import ProfilingComparision, ProfilingExperiment, ProfilingRun


class ProfilingContractTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="contract-user")
        self.client.force_authenticate(user=self.user)
        self.experiment = ProfilingExperiment.objects.create(
            name="contract", created_by=self.user, modified_by=self.user,
            scenario_name="fixture",
        )
        self.run = ProfilingRun.objects.create(experiment=self.experiment, requested_by=self.user)

    def test_canonical_run_endpoint_and_removed_alias(self):
        response = self.client.get("/profiling_exps/runs/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [self.run.id])
        self.assertEqual(self.client.get("/profiling_exps/generated_runs/").status_code, 404)

    def test_run_list_is_owner_scoped_and_requires_auth(self):
        other = get_user_model().objects.create_user(username="other")
        self.client.force_authenticate(user=other)
        self.assertEqual(self.client.get("/profiling_exps/runs/").json(), [])
        self.assertEqual(self.client.get(f"/profiling_exps/runs/{self.run.id}/").status_code, 404)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/profiling_exps/runs/").status_code, 401)

    def test_comparison_creation_rolls_back_when_rows_fail(self):
        with patch("profiling_exps.api.ProfilingComparisionRun.objects.bulk_create", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("profiling_comparision_list_create"),
                    {"name": "atomic", "run_ids": [self.run.id]}, format="json",
                )
        self.assertFalse(ProfilingComparision.objects.filter(name="atomic").exists())

    def test_duplicate_comparison_leaves_original_rows_unchanged(self):
        url = reverse("profiling_comparision_list_create")
        payload = {"name": "same-name", "run_ids": [self.run.id]}
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 201)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 400)
        self.assertEqual(ProfilingComparision.objects.get(name="same-name").runs.count(), 1)

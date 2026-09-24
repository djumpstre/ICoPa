from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from runtime_env.models import RuntimeEnvironment
from scenarios.models import Scenario

from .models import ProfilingExperiment, ProfilingRun


User = get_user_model()


class ProfilingGenerationVersioningTests(APITestCase):
    def setUp(self):
        artifacts = TemporaryDirectory()
        self.addCleanup(artifacts.cleanup)
        isolated_settings = override_settings(BASE_DIR=Path(artifacts.name), MEDIA_ROOT=artifacts.name)
        isolated_settings.enable()
        self.addCleanup(isolated_settings.disable)
        self.user = User.objects.create_user(username="agent", password="testpass123")
        self.client.force_authenticate(user=self.user)
        Scenario.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="cross-site-zenoh-vm",
            kind="Scenario",
            metadata={"name": "cross-site-zenoh-vm"},
            nodes=[
                {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
            ],
            graph={"edges": [{"from": "vm_home", "to": "cloud_vm_bw"}]},
            runtime_env=[{"name": "zenoh-unit8-vm"}],
            raw_payload={
                "kind": "Scenario",
                "metadata": {"name": "cross-site-zenoh-vm"},
                "spec": {
                    "nodes": [
                        {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                        {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
                    ],
                    "runtimeEnv": [{"name": "zenoh-unit8-vm"}],
                    "phaseTemplates": [
                        {
                            "name": "preflight",
                            "mode": "sequential",
                            "actions": [
                                {"type": "check_connectivity", "targetRef": "vm:cloud_vm_bw"},
                                {"type": "check_connectivity", "targetRef": "vm:vm_home"},
                            ],
                        },
                    ],
                },
            },
        )

    @staticmethod
    def _single_exp_yaml(name: str = "exp-versioned") -> bytes:
        return (
            f"""
kind: ExperimentPlan
metadata:
  name: {name}
  scenarioRef: cross-site-zenoh-vm
spec:
  execution:
    mode: single_run
    stopOnFailure: true
  selections:
    runtimeEnv: zenoh-unit8-vm
  phases:
    - name: preflight
      useTemplate: preflight
""".encode(
                "utf-8"
            )
        )

    @staticmethod
    def _variant_exp_yaml(
        *,
        name: str,
        payload_values: list[str],
        publish_rate_values: list[int],
    ) -> bytes:
        payload_list = ", ".join([f'"{item}"' for item in payload_values]) or '"64KB"'
        rate_list = ", ".join([str(item) for item in publish_rate_values]) or "20"
        return (
            f"""
kind: ExperimentPlan
metadata:
  name: {name}
  scenarioRef: cross-site-zenoh-vm
spec:
  execution:
    mode: all_variants
    stopOnFailure: true
  exploration:
    axes:
      payload_size: [{payload_list}]
      frequency_hz: [{rate_list}]
  selections:
    runtimeEnv: zenoh-unit8-vm
  phases:
    - name: preflight
      useTemplate: preflight
""".encode(
                "utf-8"
            )
        )

    def _upload(self, content: bytes):
        upload = SimpleUploadedFile("exp_plan.yaml", content, content_type="application/x-yaml")
        return self.client.post(reverse("profiling_exp_list_create"), data={"file": upload}, format="multipart")

    def _generate(self, exp_name: str):
        return self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": exp_name}),
            data={},
            format="json",
        )

    def test_generate_assigns_incremental_gen_version(self):
        self._upload(self._single_exp_yaml())

        first = self._generate("exp-versioned")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json().get("gen_version"), 1)

        second = self._generate("exp-versioned")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.json().get("gen_version"), 2)

        exp = ProfilingExperiment.objects.get(created_by=self.user, name="exp-versioned")
        self.assertEqual(exp.current_gen_version, 2)
        versions = list(exp.runs.order_by("id").values_list("gen_version", flat=True))
        self.assertEqual(versions, [1, 2])

    def test_start_requires_gen_version(self):
        self._upload(self._single_exp_yaml("exp-start-required"))
        self._generate("exp-start-required")

        response = self.client.post(
            reverse("profiling_exp_start", kwargs={"exp_name": "exp-start-required"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("gen_version", response.json())

    @patch("profiling_exps.api.execute_generated_plan_run_task.apply_async")
    def test_start_only_runs_requested_generation(self, apply_async_mock):
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-gen-v2"})()
        self._upload(self._single_exp_yaml("exp-start-scoped"))
        self._generate("exp-start-scoped")
        self._generate("exp-start-scoped")

        run_v1 = ProfilingRun.objects.get(experiment__name="exp-start-scoped", gen_version=1)
        run_v2 = ProfilingRun.objects.get(experiment__name="exp-start-scoped", gen_version=2)

        response = self.client.post(
            reverse("profiling_exp_start", kwargs={"exp_name": "exp-start-scoped"}),
            data={"gen_version": 2},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json().get("started_run_id"), run_v2.id)
        self.assertEqual(response.json().get("gen_version"), 2)

        run_v1.refresh_from_db()
        run_v2.refresh_from_db()
        self.assertEqual(run_v1.status, ProfilingRun.RunStatus.PENDING)
        self.assertEqual(run_v2.status, ProfilingRun.RunStatus.RUNNING)
        self.assertEqual(run_v2.task_id, "task-gen-v2")

    @patch("profiling_exps.api.execute_generated_plan_run_task.apply_async")
    def test_start_rejects_run_id_from_different_generation(self, apply_async_mock):
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-gen-scope"})()
        self._upload(self._single_exp_yaml("exp-start-mismatch"))
        self._generate("exp-start-mismatch")
        self._generate("exp-start-mismatch")

        run_v1 = ProfilingRun.objects.get(experiment__name="exp-start-mismatch", gen_version=1)

        response = self.client.post(
            reverse("profiling_exp_start", kwargs={"exp_name": "exp-start-mismatch"}),
            data={"gen_version": 2, "run_id": run_v1.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("cannot be started for gen_version=2", str(response.json()))

        run_v1.refresh_from_db()
        self.assertEqual(run_v1.status, ProfilingRun.RunStatus.PENDING)

    def test_run_list_supports_gen_version_filter(self):
        self._upload(self._single_exp_yaml("exp-list-filter"))
        self._generate("exp-list-filter")
        self._generate("exp-list-filter")

        response = self.client.get(
            reverse("profiling_run_list"),
            {"exp_name": "exp-list-filter", "gen_version": 1},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0].get("gen_version"), 1)

    @patch("profiling_exps.api.execute_generated_plan_run_task.apply_async")
    def test_rerun_run_creates_new_run_generation_and_starts(self, apply_async_mock):
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-rerun-1"})()
        exp = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-rerun-one",
            scenario_name="cross-site-zenoh-vm",
            generated_payload={
                "generated_name": "exp-rerun-one",
                "runs": [{"run_id": "run-001", "runtime_env_name": "zenoh-unit8-vm", "phases": []}],
                "total_generated_runs": 1,
            },
            total_generated_runs=1,
            current_gen_version=1,
        )
        source_run = ProfilingRun.objects.create(
            experiment=exp,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            gen_version=1,
            plan_run_id="run-001",
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            compiled_payload_snapshot=exp.generated_payload,
        )

        response = self.client.post(
            reverse("profiling_run_rerun", kwargs={"run_id": source_run.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        rerun_run_id = int(body.get("rerun_run_id") or 0)
        self.assertTrue(rerun_run_id > 0)
        self.assertNotEqual(rerun_run_id, source_run.id)
        self.assertEqual(body.get("gen_version"), 2)

        source_run.refresh_from_db()
        self.assertEqual(source_run.status, ProfilingRun.RunStatus.SUCCEEDED)
        rerun = ProfilingRun.objects.get(id=rerun_run_id)
        self.assertEqual(rerun.status, ProfilingRun.RunStatus.RUNNING)
        self.assertEqual(rerun.gen_version, 2)
        self.assertEqual(rerun.plan_run_id, "run-001")
        self.assertEqual(rerun.task_id, "task-rerun-1")

        exp.refresh_from_db()
        self.assertEqual(exp.current_gen_version, 2)
        self.assertGreaterEqual(exp.start_count, 1)

    def test_rerun_run_rejects_when_experiment_has_running_run(self):
        exp = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-rerun-conflict",
            scenario_name="cross-site-zenoh-vm",
            current_gen_version=1,
        )
        source_run = ProfilingRun.objects.create(
            experiment=exp,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            gen_version=1,
            plan_run_id="run-001",
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            compiled_payload_snapshot={"runs": [{"run_id": "run-001", "phases": []}], "total_generated_runs": 1},
        )
        ProfilingRun.objects.create(
            experiment=exp,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.RUNNING,
            gen_version=1,
            plan_run_id="run-002",
            stop_on_failure=True,
            total_runs=1,
        )

        response = self.client.post(
            reverse("profiling_run_rerun", kwargs={"run_id": source_run.id}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already has a RUNNING run", str(response.json()))

    def test_generate_persists_run_metadata(self):
        self._upload(
            self._variant_exp_yaml(
                name="exp-run-metadata",
                payload_values=["256KB"],
                publish_rate_values=[33],
            )
        )

        response = self._generate("exp-run-metadata")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        pending_runs = body.get("pending_runs") or []
        self.assertEqual(len(pending_runs), 1)

        run_row = pending_runs[0]
        run_metadata = run_row.get("run_metadata") if isinstance(run_row, dict) else {}
        self.assertEqual(run_metadata.get("payload_value"), "256KB")
        self.assertEqual(run_metadata.get("publish_rate_value"), 33)
        self.assertEqual(run_metadata.get("runtime_env_name"), "zenoh-unit8-vm")
        self.assertEqual(run_metadata.get("plan_run_id"), run_row.get("plan_run_id"))
        characterization = run_row.get("characterization_parameters") if isinstance(run_row, dict) else {}
        self.assertEqual(characterization.get("payload_value"), "256KB")
        self.assertEqual(characterization.get("publish_rate_value"), 33)
        self.assertEqual(characterization.get("runtime_env_name"), "zenoh-unit8-vm")
        self.assertEqual(characterization.get("plan_run_id"), run_row.get("plan_run_id"))

        generated_run = ProfilingRun.objects.get(id=run_row.get("id"))
        self.assertEqual(generated_run.run_metadata.get("payload_value"), "256KB")
        self.assertEqual(generated_run.run_metadata.get("publish_rate_value"), 33)
        self.assertEqual(generated_run.characterization_parameters.get("payload_value"), "256KB")
        self.assertEqual(generated_run.characterization_parameters.get("publish_rate_value"), 33)
        self.assertEqual(generated_run.get_characterization_parameters().get("payload_value"), "256KB")

    def test_generate_persists_characterization_from_runtime_rrt_defaults(self):
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm",
            serializer_rrt_config_raw_yaml=(
                "icopa_sender:\n"
                "  ros__parameters:\n"
                "    payload_size: 128KB\n"
                "    frequency_hz: 15\n"
            ),
            command_preset=[],
        )
        self._upload(self._single_exp_yaml("exp-char-defaults"))

        response = self._generate("exp-char-defaults")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pending_runs = response.json().get("pending_runs") or []
        self.assertEqual(len(pending_runs), 1)
        row = pending_runs[0]
        characterization = row.get("characterization_parameters") if isinstance(row, dict) else {}
        self.assertEqual(characterization.get("payload_value"), "128KB")
        self.assertEqual(characterization.get("publish_rate_value"), 15)

    def test_generate_replace_run_id_replaces_one_pending_run(self):
        self._upload(
            self._variant_exp_yaml(
                name="exp-replace-run",
                payload_values=["32KB", "64KB"],
                publish_rate_values=[25],
            )
        )
        first_response = self._generate("exp-replace-run")
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        first_pending_runs = first_response.json().get("pending_runs") or []
        self.assertEqual(len(first_pending_runs), 2)

        replace_target = first_pending_runs[0]
        replaced_run_id = replace_target.get("id")
        replaced_plan_run_id = replace_target.get("plan_run_id")
        self.assertTrue(replaced_run_id)
        self.assertTrue(replaced_plan_run_id)

        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-replace-run"}),
            data={"replace_run_id": replaced_run_id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body.get("replace_mode"), True)
        self.assertEqual(body.get("replaced_run_id"), replaced_run_id)
        self.assertEqual(body.get("replaced_plan_run_id"), replaced_plan_run_id)
        self.assertEqual(body.get("created_pending_runs"), 1)
        self.assertEqual(body.get("total_generated_runs"), 1)
        self.assertEqual(body.get("gen_version"), 2)

        replacement_rows = body.get("pending_runs") or []
        self.assertEqual(len(replacement_rows), 1)
        replacement = replacement_rows[0]
        self.assertNotEqual(replacement.get("id"), replaced_run_id)
        self.assertEqual(replacement.get("plan_run_id"), replaced_plan_run_id)

        self.assertFalse(ProfilingRun.objects.filter(id=replaced_run_id).exists())
        self.assertEqual(
            ProfilingRun.objects.filter(experiment__name="exp-replace-run", gen_version=2).count(),
            1,
        )

    def test_generate_replace_run_id_requires_pending_run(self):
        self._upload(
            self._variant_exp_yaml(
                name="exp-replace-status",
                payload_values=["64KB"],
                publish_rate_values=[20],
            )
        )
        generate_response = self._generate("exp-replace-status")
        self.assertEqual(generate_response.status_code, status.HTTP_200_OK)
        run_id = generate_response.json().get("pending_runs", [])[0].get("id")
        run_obj = ProfilingRun.objects.get(id=run_id)
        run_obj.status = ProfilingRun.RunStatus.SUCCEEDED
        run_obj.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-replace-status"}),
            data={"replace_run_id": run_id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Only pre-generated PENDING runs can be replaced.", str(response.json()))

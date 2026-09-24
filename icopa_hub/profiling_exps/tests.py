from unittest.mock import patch
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SSHCredential, VM
from runtime_env.models import RuntimeEnvironment
from scenarios.models import Scenario, ScenarioValidationRun

from .models import ProfilingComparision, ProfilingExperiment, ProfilingRun



User = get_user_model()


class ProfilingExperimentAPITestCase(APITestCase):
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
                        {
                            "name": "setup_routing_to_cloud",
                            "mode": "sequential",
                            "actions": [
                                {"type": "run_runtime_preset", "preset": "routing/cloud_router", "targetRef": "vm:cloud_vm_bw"},
                                {"type": "wait", "parameters": {"seconds": 3}},
                                {"type": "run_runtime_preset", "preset": "routing/local_router", "targetRef": "vm:vm_home"},
                                {"type": "wait", "parameters": {"seconds": 3}},
                            ],
                        },
                        {
                            "name": "probe",
                            "mode": "sequential",
                            "actions": [
                                {"type": "run_runtime_preset", "preset": "profiling/latency_responder", "targetRef": "vm:cloud_vm_bw"},
                                {"type": "wait", "parameters": {"seconds": 3}},
                                {"type": "run_runtime_preset", "preset": "profiling/latency_sender", "targetRef": "vm:vm_home"},
                            ],
                        },
                        {
                            "name": "collect_metrics",
                            "mode": "sequential",
                            "actions": [
                                {"type": "collect_metrics", "targetRef": "vm:vm_home"},
                                {"type": "collect_metrics", "targetRef": "vm:cloud_vm_bw"},
                            ],
                        },
                    ],
                },
            },
        )

    def _upload(self, content: bytes):
        upload = SimpleUploadedFile("exp_plan.yaml", content, content_type="application/x-yaml")
        return self.client.post(reverse("profiling_exp_list_create"), data={"file": upload}, format="multipart")

    def _single_exp_yaml(self) -> bytes:
        return b"""
kind: ExperimentPlan
metadata:
  name: exp-one
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
    - name: setup_routing_to_cloud
      useTemplate: setup_routing_to_cloud
    - name: probe
      useTemplate: probe
    - name: collect_metrics
      useTemplate: collect_metrics
"""

    def _create_inventory(self):
        credential = SSHCredential.objects.create(
            created_by=self.user,
            name="cred-one",
            key_path="/tmp/id_rsa",
            user_name="root",
        )
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_home",
            address="10.0.0.2",
            user_name="root",
            credential=credential,
            status=VM.VMStatus.ACTIVE,
        )
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="cloud_vm_bw",
            address="10.0.0.3",
            user_name="root",
            credential=credential,
            status=VM.VMStatus.ACTIVE,
        )

    def _create_runtime_env(self):
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm",
            serializer_rrt_config_raw_yaml=(
                "icopa_sender:\n"
                "  ros__parameters:\n"
                "    payload_size: 1024KB\n"
                "    frequency_hz: 30\n"
                "    json_duration_sec: 20\n"
                "icopa_responder:\n"
                "  ros__parameters:\n"
                "    response_payload_size: 256KB\n"
            ),
            command_preset=[
                {"group": "profiling", "name": "latency_responder", "executor": "ssh", "command": "echo responder"},
                {"group": "profiling", "name": "latency_sender", "executor": "ssh", "command": "echo sender"},
                {"group": "routing", "name": "cloud_router", "executor": "ssh", "command": "echo cloud-router"},
                {"group": "routing", "name": "local_router", "executor": "ssh", "command": "echo local-router"},
            ],
        )

    def test_create_experiment_from_yaml(self):
        response = self._upload(self._single_exp_yaml())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        exp = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        self.assertEqual(exp.scenario_name, "cross-site-zenoh-vm")
        self.assertEqual(exp.status, ProfilingExperiment.ExperimentStatus.NEW)
        self.assertEqual(exp.phases[0]["name"], "preflight")

    def test_topology_overview_includes_edge_validation_latency(self):
        scenario = Scenario.objects.get(created_by=self.user, name="cross-site-zenoh-vm")
        scenario.graph = {"edges": [{"name": "robot_to_cloud", "from": "vm_home", "to": "cloud_vm_bw"}]}
        scenario.save(update_fields=["graph", "updated_at"])

        exp = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-topology-edge-latency",
            scenario_name=scenario.name,
            raw_payload={
                "kind": "ExperimentPlan",
                "metadata": {"name": "exp-topology-edge-latency", "scenarioRef": scenario.name},
                "spec": {
                    "execution": {"mode": "single_run"},
                    "selections": {"runtimeEnv": "zenoh-unit8-vm"},
                    "phases": [
                        {"name": "probe", "useTemplate": "probe", "edgeRefs": ["robot_to_cloud"]},
                    ],
                },
            },
            phases=[
                {"name": "probe", "useTemplate": "probe", "edgeRefs": ["robot_to_cloud"]},
            ],
            execution={"mode": "single_run"},
            selections={"runtimeEnv": "zenoh-unit8-vm"},
        )

        graph_result = {
            "ok": True,
            "checked_edges": 1,
            "results": [
                {
                    "edge_name": "robot_to_cloud",
                    "source_node": "vm_home",
                    "target_node": "cloud_vm_bw",
                    "ok": True,
                    "status": "SUCCESS",
                    "message": "ok",
                    "metrics": {"latency_avg_ms": 14.2},
                }
            ],
        }
        ScenarioValidationRun.objects.create(
            scenario=scenario,
            requested_by=self.user,
            check_actions=False,
            check_nodes=False,
            check_graph=True,
            status=ScenarioValidationRun.RunStatus.SUCCEEDED,
            result_data={
                "check_graph": True,
                "checks": {"graph": graph_result},
            },
        )

        overview = exp.get_topology_overview(user=self.user)
        self.assertEqual(overview["selected_edge_names"], ["robot_to_cloud"])
        self.assertEqual(len(overview["edges"]), 1)
        edge = overview["edges"][0]
        self.assertEqual(edge["name"], "robot_to_cloud")
        self.assertEqual(edge["validation"]["status"], "SUCCESS")
        self.assertEqual(edge["validation"]["metrics"]["latency_avg_ms"], 14.2)

    def test_experiment_status_derived_from_run_states(self):
        exp = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-status-flow",
            scenario_name="cross-site-zenoh-vm",
            generated_payload={"runs": [{"run_id": "run-001"}]},
            total_generated_runs=1,
            status=ProfilingExperiment.ExperimentStatus.NEW,
        )

        exp.sync_status_from_runs(save=True, modified_by=self.user)
        exp.refresh_from_db()
        self.assertEqual(exp.status, ProfilingExperiment.ExperimentStatus.WAITING)

        run = ProfilingRun.objects.create(
            experiment=exp,
            requested_by=self.user,
            plan_run_id="run-001",
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=1,
        )
        exp.sync_status_from_runs(save=True, modified_by=self.user)
        exp.refresh_from_db()
        self.assertEqual(exp.status, ProfilingExperiment.ExperimentStatus.WAITING)

        run.status = ProfilingRun.RunStatus.RUNNING
        run.save(update_fields=["status", "updated_at"])
        exp.sync_status_from_runs(save=True, modified_by=self.user)
        exp.refresh_from_db()
        self.assertEqual(exp.status, ProfilingExperiment.ExperimentStatus.RUNNING)

        run.status = ProfilingRun.RunStatus.SUCCEEDED
        run.save(update_fields=["status", "updated_at"])
        exp.sync_status_from_runs(save=True, modified_by=self.user)
        exp.refresh_from_db()
        self.assertEqual(exp.status, ProfilingExperiment.ExperimentStatus.FINISHED)

        run.status = ProfilingRun.RunStatus.FAILED
        run.save(update_fields=["status", "updated_at"])
        exp.sync_status_from_runs(save=True, modified_by=self.user)
        exp.refresh_from_db()
        self.assertEqual(exp.status, ProfilingExperiment.ExperimentStatus.ERROR)

    def test_create_experiment_rejects_phase_alias_lists(self):
        content = b"""
kind: ExperimentPlan
metadata:
  name: exp-alias-phases
  scenarioRef: cross-site-zenoh-vm
spec:
  execution:
    mode: single_run
  selections:
    runtimeEnv: zenoh-unit8-vm
  phasesPrerun:
    - name: setup_routing_to_cloud
      useTemplate: setup_routing_to_cloud
  phasesInEachRun:
    - name: probe
      useTemplate: probe
    - name: collect_metrics
      useTemplate: collect_metrics
"""
        upload_response = self._upload(content)
        self.assertEqual(upload_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("spec.phasesPrerun/phasesInEachRun", str(upload_response.json()))

    def test_create_requires_existing_scenario(self):
        content = b"""
kind: ExperimentPlan
metadata:
  name: exp-bad
  scenarioRef: no-such-scenario
spec:
  execution: {}
  selections: {}
  phases:
    - name: preflight
      mode: sequential
      actions: []
"""
        response = self._upload(content)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no-such-scenario", str(response.json()))

    def test_list_contains_generated_flag(self):
        self._upload(self._single_exp_yaml())

        list_resp = self.client.get(reverse("profiling_exp_list_create"))
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(list_resp.json()[0]["name"], "exp-one")
        self.assertEqual(list_resp.json()[0]["has_generated_plan"], False)

    def test_generate_concrete_plan_success(self):
        self._upload(self._single_exp_yaml())

        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["experiment"]["name"], "exp-one")
        self.assertEqual(body["total_generated_runs"], 1)
        self.assertTrue(isinstance(body.get("rrt_variable_checks"), dict))
        self.assertTrue(body["rrt_variable_checks"].get("ok"))

        run_summaries = body["run_summaries"]
        self.assertTrue(len(run_summaries) >= 1)
        first_run_phases = run_summaries[0]["phases"]
        self.assertEqual(first_run_phases[0]["name"], "preflight")
        self.assertIn("check_connectivity@cloud_vm_bw", first_run_phases[0]["actions"])
        self.assertIn("wait(3s)", first_run_phases[1]["actions"])
        self.assertIn("run_runtime_preset:profiling/latency_sender@vm_home", first_run_phases[2]["actions"])

        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        self.assertTrue(generated.has_generated_plan())
        compiled_run = ProfilingRun.objects.get(experiment=generated, requested_by=self.user)
        self.assertEqual(compiled_run.status, ProfilingRun.RunStatus.PENDING)
        self.assertEqual(compiled_run.total_runs, 1)

        list_resp = self.client.get(reverse("profiling_exp_list_create"))
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(list_resp.json()[0]["has_generated_plan"], True)

    def test_generate_concrete_plan_expands_runs_per_edge(self):
        Scenario.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="multi-edge-zenoh-vm",
            kind="Scenario",
            metadata={"name": "multi-edge-zenoh-vm"},
            nodes=[
                {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                {"name": "vm_lab_edge", "kind": "vm", "ref": "vm_lab_edge"},
                {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
            ],
            graph={
                "edges": [
                    {"name": "robot_to_edge", "from": "vm_home", "to": "vm_lab_edge"},
                    {"name": "edge_to_cloud", "from": "vm_lab_edge", "to": "cloud_vm_bw"},
                ]
            },
            runtime_env=[{"name": "zenoh-unit8-vm"}],
            raw_payload={
                "kind": "Scenario",
                "metadata": {"name": "multi-edge-zenoh-vm"},
                "spec": {
                    "nodes": [
                        {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                        {"name": "vm_lab_edge", "kind": "vm", "ref": "vm_lab_edge"},
                        {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
                    ],
                    "graph": {
                        "edges": [
                            {"name": "robot_to_edge", "from": "vm_home", "to": "vm_lab_edge", "type": "robot2edge"},
                            {"name": "edge_to_cloud", "from": "vm_lab_edge", "to": "cloud_vm_bw", "type": "edge2cloud"},
                        ]
                    },
                    "runtimeEnv": [{"name": "zenoh-unit8-vm"}],
                    "phaseTemplates": [
                        {
                            "name": "probe",
                            "mode": "sequential",
                            "actions": [
                                {
                                    "type": "run_runtime_preset",
                                    "preset": "profiling/latency_responder",
                                    "targetRef": "vm:${edge.to}",
                                },
                                {
                                    "type": "run_runtime_preset",
                                    "preset": "profiling/latency_sender",
                                    "targetRef": "vm:${edge.from}",
                                    "parameters": {"peer_node": "${edge.to}"},
                                },
                            ],
                        }
                    ],
                },
            },
        )
        content = b"""
kind: ExperimentPlan
metadata:
  name: exp-edge-runs
  scenarioRef: multi-edge-zenoh-vm
spec:
  execution:
    stopOnFailure: true
  exploration:
    axes:
      payload_size: ["16KB", "32KB"]
  selections:
    runtimeEnv: zenoh-unit8-vm
  phases:
    - name: probe
      useTemplate: probe
      edgeRefs: [robot_to_edge, edge_to_cloud]
"""
        self._upload(content)

        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-edge-runs"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["total_generated_runs"], 4)
        self.assertEqual(body["created_pending_runs"], 4)
        pending_runs = body.get("pending_runs")
        self.assertTrue(isinstance(pending_runs, list))
        self.assertEqual(len(pending_runs), 4)

        experiment = ProfilingExperiment.objects.get(created_by=self.user, name="exp-edge-runs")
        generated_payload = experiment.generated_payload if isinstance(experiment.generated_payload, dict) else {}
        runs = generated_payload.get("runs") if isinstance(generated_payload.get("runs"), list) else []
        self.assertEqual(len(runs), 4)
        edge_names = {str(item.get("edge", {}).get("name") or "") for item in runs if isinstance(item, dict)}
        self.assertEqual(edge_names, {"robot_to_edge", "edge_to_cloud"})

    def test_generate_concrete_plan_supports_exec_edge_blocks(self):
        Scenario.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="single-edge-zenoh-vm",
            kind="Scenario",
            metadata={"name": "single-edge-zenoh-vm"},
            nodes=[
                {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
            ],
            graph={"edges": [{"name": "robot_to_cloud", "from": "vm_home", "to": "cloud_vm_bw"}]},
            runtime_env=[{"name": "zenoh-unit8-vm"}],
            raw_payload={
                "kind": "Scenario",
                "metadata": {"name": "single-edge-zenoh-vm"},
                "spec": {
                    "nodes": [
                        {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                        {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
                    ],
                    "graph": {"edges": [{"name": "robot_to_cloud", "from": "vm_home", "to": "cloud_vm_bw"}]},
                    "runtimeEnv": [{"name": "zenoh-unit8-vm"}],
                    "phaseTemplates": [
                        {
                            "name": "probe",
                            "mode": "sequential",
                            "actions": [
                                {
                                    "type": "run_runtime_preset",
                                    "preset": "profiling/latency_responder",
                                    "targetRef": "vm:${edge.to}",
                                },
                                {
                                    "type": "run_runtime_preset",
                                    "preset": "profiling/latency_sender",
                                    "targetRef": "vm:${edge.from}",
                                },
                            ],
                        }
                    ],
                },
            },
        )
        content = b"""
kind: ExperimentPlan
metadata:
  name: exp-exec-edge
  scenarioRef: single-edge-zenoh-vm
spec:
  execution:
    stopOnFailure: true
  selections:
    runtimeEnv: zenoh-unit8-vm
  execEdge:
    - edgeRefs: [robot_to_cloud]
      phases:
        - name: probe
          useTemplate: probe
"""
        self._upload(content)

        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-exec-edge"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["total_generated_runs"], 1)
        run_summaries = body.get("run_summaries")
        self.assertTrue(isinstance(run_summaries, list) and len(run_summaries) == 1)
        edge_payload = run_summaries[0].get("edge")
        self.assertTrue(isinstance(edge_payload, dict))
        self.assertEqual(edge_payload.get("name"), "robot_to_cloud")

    def test_generate_rejects_mismatched_rrt_variant_variables(self):
        self._create_runtime_env()
        RuntimeEnvironment.objects.filter(created_by=self.user, name="zenoh-unit8-vm").update(
            serializer_rrt_config_raw_yaml="icopa_responder:\n  ros__parameters:\n    response_payload_size: 256KB\n"
        )
        content = b"""
kind: ExperimentPlan
metadata:
  name: exp-rrt-mismatch
  scenarioRef: cross-site-zenoh-vm
spec:
  execution:
    mode: single_run
  exploration:
    axes:
      payload_size: ["512KB"]
  selections:
    runtimeEnv: zenoh-unit8-vm
  phases:
    - name: preflight
      useTemplate: preflight
"""
        self._upload(content)
        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-rrt-mismatch"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("rrt_variables", response.json())

    def test_generate_rejects_execution_runs_field(self):
        content = b"""
kind: ExperimentPlan
metadata:
  name: exp-too-many
  scenarioRef: cross-site-zenoh-vm
spec:
  execution:
    mode: single_run
    runs: 3
  selections:
    runtimeEnv: zenoh-unit8-vm
  phases:
    - name: preflight
      useTemplate: preflight
"""
        self._upload(content)

        response = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-too-many"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("execution.runs is not supported", str(response.json()))

    def test_upload_update_invalidates_existing_generated_plans(self):
        self._upload(self._single_exp_yaml())
        gen_resp = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self.assertEqual(gen_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            ProfilingExperiment.objects.get(created_by=self.user, name="exp-one").has_generated_plan()
        )

        updated_yaml = b"""
kind: ExperimentPlan
metadata:
  name: exp-one
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
"""
        upload_resp = self._upload(updated_yaml)
        self.assertEqual(upload_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(upload_resp.json()["invalidated"]["runs"], 1)
        self.assertFalse(
            ProfilingExperiment.objects.get(created_by=self.user, name="exp-one").has_generated_plan()
        )

        list_resp = self.client.get(reverse("profiling_exp_list_create"))
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(list_resp.json()[0]["has_generated_plan"], False)

    @patch("profiling_exps.api.execute_generated_plan_run_task.apply_async")
    def test_start_generated_plan_queues_run(self, apply_async_mock):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-generated-1"})()

        response = self.client.post(
            reverse("profiling_exp_start", kwargs={"exp_name": "exp-one"}),
            data={"gen_version": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["execution_mode"], "queued")

        generated_run = ProfilingRun.objects.get(experiment__name="exp-one")
        experiment = ProfilingExperiment.objects.get(name="exp-one", created_by=self.user)
        self.assertEqual(ProfilingRun.objects.filter(experiment__name="exp-one").count(), 1)
        self.assertEqual(generated_run.status, ProfilingRun.RunStatus.RUNNING)
        self.assertEqual(experiment.status, ProfilingExperiment.ExperimentStatus.RUNNING)
        self.assertEqual(generated_run.task_id, "task-generated-1")
        self.assertEqual(generated_run.total_runs, 1)
        self.assertEqual(body.get("started_run_id"), generated_run.id)

    @patch("profiling_exps.api.execute_generated_plan_run_task.apply_async")
    def test_start_generated_plan_supports_run_id(self, apply_async_mock):
        exp = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-start-select",
            scenario_name="cross-site-zenoh-vm",
            generated_payload={"runs": [{"run_id": "run-001"}, {"run_id": "run-002"}]},
        )
        run_one = ProfilingRun.objects.create(
            experiment=exp,
            requested_by=self.user,
            plan_run_id="run-001",
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=1,
        )
        run_two = ProfilingRun.objects.create(
            experiment=exp,
            requested_by=self.user,
            plan_run_id="run-002",
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=1,
        )
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-generated-2"})()

        response = self.client.post(
            reverse("profiling_exp_start", kwargs={"exp_name": exp.name}),
            data={"run_id": run_two.id, "gen_version": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body.get("started_run_id"), run_two.id)
        self.assertEqual(body.get("remaining_pending_runs"), 1)

        run_one.refresh_from_db()
        run_two.refresh_from_db()
        self.assertEqual(run_one.status, ProfilingRun.RunStatus.PENDING)
        self.assertEqual(run_two.status, ProfilingRun.RunStatus.RUNNING)
        self.assertEqual(run_two.task_id, "task-generated-2")

    def test_generated_run_list_returns_compiled_run_after_generate(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )

        response = self.client.get(reverse("profiling_run_list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["experiment_name"], "exp-one")
        self.assertEqual(body[0]["status"], ProfilingRun.RunStatus.PENDING)
        self.assertEqual(body[0]["task_id"], "")

    def test_regenerate_preserves_previous_generation(self):
        self._upload(self._single_exp_yaml())
        first_resp = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self.assertEqual(first_resp.status_code, status.HTTP_200_OK)
        first_run_id = first_resp.json()["pending_runs"][0]["id"]
        self.assertTrue(first_run_id)

        second_resp = self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self.assertEqual(second_resp.status_code, status.HTTP_200_OK)
        second_body = second_resp.json()
        second_run_id = second_body["pending_runs"][0]["id"]
        self.assertTrue(second_run_id)
        self.assertNotEqual(first_run_id, second_run_id)
        self.assertEqual(second_body["gen_version"], 2)
        self.assertEqual(second_body["purged_pending_runs"], 0)

        runs = list(
            ProfilingRun.objects.filter(experiment__name="exp-one").order_by("-created_at")
        )
        self.assertEqual(len(runs), 2)
        self.assertEqual({run.gen_version for run in runs}, {1, 2})
        self.assertEqual(runs[0].status, ProfilingRun.RunStatus.PENDING)

    def test_generated_run_detail_returns_run_by_id(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.create(
            experiment=generated,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=0,
            failed_runs=0,
            task_id="task-detail-1",
            results=[],
        )

        response = self.client.get(
            reverse("profiling_run_detail", kwargs={"run_id": generated_run.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["id"], generated_run.id)
        self.assertEqual(body["experiment_name"], "exp-one")
        self.assertEqual(body["task_id"], "task-detail-1")

    def test_generated_run_detail_patch_updates_note(self):
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-note-update",
            scenario_name="cross-site-zenoh-vm",
        )
        generated_run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=0,
            failed_runs=0,
            task_id="task-note-update",
        )

        patch_response = self.client.patch(
            reverse("profiling_run_detail", kwargs={"run_id": generated_run.id}),
            data={"note": "compare with low payload baseline"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        patch_body = patch_response.json()
        self.assertEqual(patch_body.get("run", {}).get("id"), generated_run.id)
        self.assertEqual(patch_body.get("run", {}).get("note"), "compare with low payload baseline")

        generated_run.refresh_from_db()
        self.assertEqual(generated_run.note, "compare with low payload baseline")

        detail_response = self.client.get(
            reverse("profiling_run_detail", kwargs={"run_id": generated_run.id})
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.json().get("note"), "compare with low payload baseline")

    @patch("profiling_exps.api.execute_generated_plan_run_task.apply_async")
    def test_generated_run_rerun_replace_current_reuses_same_run(self, apply_async_mock):
        apply_async_mock.return_value = type("TaskResult", (), {"id": "task-rerun-replace-current"})()
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-rerun-replace-current",
            scenario_name="cross-site-zenoh-vm",
            current_gen_version=2,
            start_count=3,
        )
        generated_run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            gen_version=2,
            plan_run_id="run-001",
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            task_id="task-old",
            error_message="old error",
            compiled_payload_snapshot={
                "total_generated_runs": 1,
                "runs": [
                    {
                        "run_id": "run-001",
                        "runtime_env_name": "zenoh-unit8-vm",
                        "phases": [],
                    }
                ],
            },
            run_metadata={"plan_run_id": "run-001"},
            characterization_parameters={"publish_rate_value": 10},
            phase_action_execution_status={"runs": {"run-001": {"status": "FAILED"}}},
            rrt_config_execution_snapshot={"runs": {"run-001": {"runtime_env_name": "zenoh-unit8-vm"}}},
            results=[{"run_id": "run-001", "status": "FAILED"}],
        )

        response = self.client.post(
            reverse("profiling_run_rerun", kwargs={"run_id": generated_run.id}),
            data={"replace_current_run": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body.get("source_run_id"), generated_run.id)
        self.assertEqual(body.get("rerun_run_id"), generated_run.id)
        self.assertEqual(body.get("replaced_current_run"), True)
        self.assertEqual(body.get("gen_version"), 2)
        self.assertEqual(body.get("task_id"), "task-rerun-replace-current")

        generated_run.refresh_from_db()
        experiment.refresh_from_db()
        self.assertEqual(generated_run.id, body.get("rerun_run_id"))
        self.assertEqual(generated_run.status, ProfilingRun.RunStatus.RUNNING)
        self.assertEqual(generated_run.gen_version, 2)
        self.assertEqual(generated_run.completed_runs, 0)
        self.assertEqual(generated_run.failed_runs, 0)
        self.assertEqual(generated_run.task_id, "task-rerun-replace-current")
        self.assertEqual(generated_run.error_message, "")
        self.assertEqual(len(generated_run.results), 0)
        self.assertEqual(ProfilingRun.objects.filter(experiment=experiment).count(), 1)
        self.assertEqual(experiment.current_gen_version, 2)
        self.assertEqual(experiment.start_count, 4)

    def test_generated_run_detail_includes_run_metric_file_urls(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.create(
            experiment=generated,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            task_id="task-detail-metrics",
            results=[],
        )

        metrics_rel = f"static/profiling_exps/exp-one/exp-one-generated/run_{generated_run.id}/metrics"
        vm_home = Path(settings.BASE_DIR) / metrics_rel / "run-001" / "vm_home"
        vm_home.mkdir(parents=True, exist_ok=True)
        (vm_home / "rrt_summary.json").write_text("{}", encoding="utf-8")
        (vm_home / "rrt_config.yaml").write_text("icopa_sender: {}", encoding="utf-8")
        (vm_home / "rrt_all.csv").write_text("id,t_send_ns,t_recv_ns,rtt_ns\n1,1,2,1\n", encoding="utf-8")

        generated_run.collected_metrics_path = metrics_rel
        generated_run.results = [{"run_id": "run-001", "metrics_backend_dir": f"{metrics_rel}/run-001"}]
        generated_run.save(update_fields=["collected_metrics_path", "results", "updated_at"])

        response = self.client.get(reverse("profiling_run_detail", kwargs={"run_id": generated_run.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        run_metric_files = body.get("run_metric_files") if isinstance(body, dict) else []
        self.assertTrue(isinstance(run_metric_files, list) and len(run_metric_files) == 1)
        row = run_metric_files[0]
        self.assertEqual(row.get("run_id"), "run-001")
        self.assertEqual(
            row.get("rrt_summary_url"),
            f"/profiling_exps/runs/{generated_run.id}/metric_files/run-001/rrt_summary.json/",
        )
        self.assertEqual(
            row.get("rrt_config_url"),
            f"/profiling_exps/runs/{generated_run.id}/metric_files/run-001/rrt_config.yaml/",
        )
        self.assertEqual(
            row.get("rrt_all_csv_url"),
            f"/profiling_exps/runs/{generated_run.id}/metric_files/run-001/rrt_all.csv/",
        )

    def test_generated_run_detail_includes_rrt_summaries_json(self):
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-rrt-summary-json",
            scenario_name="cross-site-zenoh-vm",
        )
        generated_run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            task_id="task-detail-summary",
            results=[],
        )

        metrics_rel = f"static/profiling_exps/exp-one/exp-one-generated/run_{generated_run.id}/metrics"
        vm_home = Path(settings.BASE_DIR) / metrics_rel / "run-001" / "vm_home"
        vm_home.mkdir(parents=True, exist_ok=True)
        (vm_home / "rrt_summary.json").write_text(
            (
                "{"
                "\"duration_sec\": 60,"
                "\"loss_percent\": 0.5,"
                "\"latency_ms\": {\"avg\": 10.1, \"p95\": 14.2, \"p99\": 20.3}"
                "}"
            ),
            encoding="utf-8",
        )

        generated_run.collected_metrics_path = metrics_rel
        generated_run.results = [{"run_id": "run-001", "metrics_backend_dir": f"{metrics_rel}/run-001"}]
        generated_run.save(update_fields=["collected_metrics_path", "results", "updated_at"])

        response = self.client.get(reverse("profiling_run_detail", kwargs={"run_id": generated_run.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        rows = body.get("rrt_summaries") if isinstance(body, dict) else []
        self.assertTrue(isinstance(rows, list) and len(rows) == 1)
        row = rows[0]
        self.assertEqual(row.get("run_id"), "run-001")
        summary = row.get("rrt_summary_json") if isinstance(row, dict) else {}
        self.assertTrue(isinstance(summary, dict))
        latency = summary.get("latency_ms") if isinstance(summary.get("latency_ms"), dict) else {}
        self.assertEqual(summary.get("loss_percent"), 0.5)
        self.assertEqual(latency.get("avg"), 10.1)
        self.assertEqual(latency.get("p95"), 14.2)
        self.assertEqual(latency.get("p99"), 20.3)

    def test_generated_run_detail_includes_rrt_configs_json(self):
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-rrt-json",
            scenario_name="cross-site-zenoh-vm",
        )
        generated_run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            rrt_config_execution_snapshot={
                "runs": {
                    "run-001": {
                        "runtime_rrt_config_path": "static/runtime/zenoh-unit8-vm/4/serializer_rrt_config.yaml",
                        "actions": [
                            {
                                "preset": "profiling/latency_sender",
                                "rrt_config_yaml": (
                                    "icopa_sender:\n"
                                    "  ros__parameters:\n"
                                    "    payload_size: 16KB\n"
                                    "    frequency_hz: 60\n"
                                ),
                            }
                        ],
                    }
                }
            },
        )

        response = self.client.get(reverse("profiling_run_detail", kwargs={"run_id": generated_run.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        rows = body.get("rrt_configs") if isinstance(body, dict) else []
        self.assertTrue(isinstance(rows, list) and len(rows) == 1)
        row = rows[0]
        self.assertEqual(row.get("run_id"), "run-001")
        self.assertEqual(
            row.get("runtime_rrt_config_path"),
            "static/runtime/zenoh-unit8-vm/4/serializer_rrt_config.yaml",
        )
        config_json = row.get("rrt_config_json")
        self.assertTrue(isinstance(config_json, dict))
        sender = config_json.get("icopa_sender") if isinstance(config_json, dict) else {}
        params = sender.get("ros__parameters") if isinstance(sender, dict) else {}
        self.assertEqual(params.get("payload_size"), "16KB")
        self.assertEqual(params.get("frequency_hz"), 60)

    def test_generated_run_metric_file_endpoint_returns_metric_file(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.create(
            experiment=generated,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            results=[],
        )
        metrics_rel = f"static/profiling_exps/exp-one/exp-one-generated/run_{generated_run.id}/metrics"
        vm_home = Path(settings.BASE_DIR) / metrics_rel / "run-001" / "vm_home"
        vm_home.mkdir(parents=True, exist_ok=True)
        expected_text = "{\"packets_sent\": 1}\n"
        (vm_home / "rrt_summary.json").write_text(expected_text, encoding="utf-8")
        generated_run.collected_metrics_path = metrics_rel
        generated_run.results = [{"run_id": "run-001", "metrics_backend_dir": f"{metrics_rel}/run-001"}]
        generated_run.save(update_fields=["collected_metrics_path", "results", "updated_at"])

        response = self.client.get(
            reverse(
                "profiling_run_metric_file",
                kwargs={
                    "run_id": generated_run.id,
                    "run_label": "run-001",
                    "file_name": "rrt_summary.json",
                },
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body_bytes = b"".join(response.streaming_content)
        self.assertEqual(body_bytes.decode("utf-8"), expected_text)

    def test_comparision_create_and_list(self):
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-cmp",
            scenario_name="cross-site-zenoh-vm",
        )
        run1 = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            plan_run_id="run-001",
        )
        run2 = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.FAILED,
            plan_run_id="run-002",
        )

        create_resp = self.client.post(
            reverse("profiling_comparision_list_create"),
            data={
                "name": "cmp-one",
                "description": "short note",
                "run_ids": [run1.id, run2.id],
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        created = create_resp.json()
        self.assertEqual(created.get("name"), "cmp-one")
        self.assertEqual(created.get("run_count"), 2)
        self.assertTrue(isinstance(created.get("runs"), list) and len(created["runs"]) == 2)
        self.assertEqual(created["runs"][0].get("deleted"), False)

        list_resp = self.client.get(reverse("profiling_comparision_list_create"))
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        rows = list_resp.json()
        self.assertTrue(isinstance(rows, list) and len(rows) >= 1)
        self.assertEqual(rows[0].get("name"), "cmp-one")

    def test_comparision_keeps_rows_when_run_deleted(self):
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-cmp-delete",
            scenario_name="cross-site-zenoh-vm",
        )
        run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            plan_run_id="run-010",
        )
        comparision = ProfilingComparision.objects.create(
            name="cmp-delete",
            description="",
            created_by=self.user,
            modified_by=self.user,
        )
        comparision.runs.create(
            run=run,
            run_id_snapshot=run.id,
            experiment_name_snapshot=experiment.name,
            plan_run_id_snapshot=run.plan_run_id,
            status_snapshot=run.status,
        )

        delete_resp = self.client.delete(reverse("profiling_run_detail", kwargs={"run_id": run.id}))
        self.assertEqual(delete_resp.status_code, status.HTTP_200_OK)

        list_resp = self.client.get(reverse("profiling_comparision_list_create"))
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        rows = list_resp.json()
        self.assertTrue(isinstance(rows, list) and len(rows) >= 1)
        match = next((row for row in rows if int(row.get("id") or 0) == comparision.id), None)
        self.assertTrue(isinstance(match, dict))
        run_rows = match.get("runs") if isinstance(match, dict) else []
        self.assertTrue(isinstance(run_rows, list) and len(run_rows) == 1)
        self.assertEqual(run_rows[0].get("run_id"), run.id)
        self.assertEqual(run_rows[0].get("deleted"), True)

    def test_generated_run_delete_removes_record_and_artifacts(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.create(
            experiment=generated,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.SUCCEEDED,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=1,
            failed_runs=0,
            task_id="task-delete-1",
            results=[],
        )

        metrics_rel = f"static/profiling_exps/exp-one/exp-one-generated/run_{generated_run.id}/metrics"
        run_root = Path(settings.BASE_DIR) / f"static/profiling_exps/exp-one/exp-one-generated/run_{generated_run.id}"
        metrics_dir = Path(settings.BASE_DIR) / metrics_rel
        artifact_file = metrics_dir / "run-001" / "vm_home" / "latency.log"
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("probe", encoding="utf-8")

        generated_run.collected_metrics_path = metrics_rel
        generated_run.results = [{"run_id": "run-001", "metrics_backend_dir": f"{metrics_rel}/run-001"}]
        generated_run.save(update_fields=["collected_metrics_path", "results", "updated_at"])

        response = self.client.delete(
            reverse("profiling_run_detail", kwargs={"run_id": generated_run.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ProfilingRun.objects.filter(id=generated_run.id).exists())
        self.assertFalse(run_root.exists())
        body = response.json()
        self.assertEqual(body["deleted_run_id"], generated_run.id)
        self.assertIn(str(run_root), body["deleted_artifact_dirs"])

    def test_generated_run_delete_rejects_active_run(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.create(
            experiment=generated,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.RUNNING,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=0,
            failed_runs=0,
            task_id="task-running-1",
            results=[],
        )

        response = self.client.delete(
            reverse("profiling_run_detail", kwargs={"run_id": generated_run.id})
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(ProfilingRun.objects.filter(id=generated_run.id).exists())

    def test_run_check_generated_plan_success(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self._create_inventory()
        self._create_runtime_env()

        response = self.client.post(
            reverse("profiling_exp_run_check", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["failed_runs"], 0)
        self.assertEqual(body["total_runs"], 1)
        self.assertTrue(len(body["run_summaries"]) >= 1)
        self.assertEqual(body["run_summaries"][0]["phases"][0]["name"], "preflight")

    def test_run_check_generated_plan_reports_runtime_env_error(self):
        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self._create_inventory()

        response = self.client.post(
            reverse("profiling_exp_run_check", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["ok"], False)
        self.assertEqual(body["failed_runs"], 1)
        self.assertIn("Runtime environment zenoh-unit8-vm not found", "\n".join(body["errors"]))

    @patch("profiling_exps.celery_tasks._execute_one_action")
    def test_generated_task_persists_phase_action_execution_status(self, execute_one_action_mock):
        from profiling_exps.api import execute_generated_plan_run_task

        execute_one_action_mock.return_value = {
            "type": "check_connectivity",
            "target": "cloud_vm_bw",
            "status": "SUCCESS",
            "success": True,
            "message": "ok",
            "debug": {},
        }

        self._upload(self._single_exp_yaml())
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self._create_inventory()
        self._create_runtime_env()

        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.get(experiment=generated, requested_by=self.user)

        result = execute_generated_plan_run_task.run(generated_run_id=generated_run.id)
        generated_run.refresh_from_db()

        self.assertEqual(result["ok"], True)
        self.assertEqual(generated_run.status, ProfilingRun.RunStatus.SUCCEEDED)
        tracker = generated_run.phase_action_execution_status
        self.assertTrue(isinstance(tracker, dict))
        self.assertTrue(isinstance(tracker.get("runs"), dict))
        run_status = tracker["runs"].get("run-001")
        self.assertTrue(isinstance(run_status, dict))
        self.assertEqual(run_status.get("status"), "SUCCEEDED")
        phases = run_status.get("phases")
        self.assertTrue(isinstance(phases, list) and len(phases) > 0)
        self.assertEqual(phases[0].get("status"), "SUCCEEDED")
        self.assertTrue(any(item.get("event") == "phase_started" for item in tracker.get("events", [])))
        rrt_snapshot = generated_run.rrt_config_execution_snapshot
        self.assertTrue(isinstance(rrt_snapshot, dict))
        self.assertTrue(isinstance((rrt_snapshot.get("runs") or {}).get("run-001"), dict))
        self.assertFalse(
            any(
                str(call.kwargs.get("phase", {}).get("name") or "") == "__precheck_connectivity__"
                for call in execute_one_action_mock.call_args_list
            )
        )

    @patch("profiling_exps.celery_tasks._execute_one_action")
    def test_generated_task_runs_implicit_connectivity_precheck_without_explicit_action(self, execute_one_action_mock):
        from profiling_exps.api import execute_generated_plan_run_task

        execute_one_action_mock.return_value = {
            "type": "run_runtime_preset",
            "target": "cloud_vm_bw",
            "status": "SUCCESS",
            "success": True,
            "message": "ok",
            "debug": {},
        }

        no_preflight_yaml = b"""
kind: ExperimentPlan
metadata:
  name: exp-one
  scenarioRef: cross-site-zenoh-vm
spec:
  execution:
    mode: single_run
    stopOnFailure: true
    connectivityPrecheck: true
  selections:
    runtimeEnv: zenoh-unit8-vm
  phases:
    - name: setup_routing_to_cloud
      useTemplate: setup_routing_to_cloud
    - name: probe
      useTemplate: probe
    - name: collect_metrics
      useTemplate: collect_metrics
"""
        self._upload(no_preflight_yaml)
        self.client.post(
            reverse("profiling_exp_generate", kwargs={"exp_name": "exp-one"}),
            data={},
            format="json",
        )
        self._create_inventory()
        self._create_runtime_env()

        generated = ProfilingExperiment.objects.get(created_by=self.user, name="exp-one")
        generated_run = ProfilingRun.objects.get(experiment=generated, requested_by=self.user)

        result = execute_generated_plan_run_task.run(generated_run_id=generated_run.id)
        self.assertTrue(result["ok"])
        self.assertTrue(
            any(
                str(call.kwargs.get("phase", {}).get("name") or "") == "__precheck_connectivity__"
                and str(call.kwargs.get("action", {}).get("type") or "") == "check_connectivity"
                for call in execute_one_action_mock.call_args_list
            )
        )

    @patch("profiling_exps.celery_tasks._execute_one_action")
    def test_generated_task_updates_inventory_vm_after_connectivity_check(self, execute_one_action_mock):
        from profiling_exps.api import execute_generated_plan_run_task

        def _fake_execute_one_action(**kwargs):
            action = kwargs.get("action") if isinstance(kwargs.get("action"), dict) else {}
            action_type = str(action.get("type") or "")
            return {
                "type": action_type,
                "target": str(action.get("target") or ""),
                "status": "SUCCESS",
                "success": True,
                "message": "ok",
                "debug": {"returncode": 0, "stderr": ""},
            }

        execute_one_action_mock.side_effect = _fake_execute_one_action

        self._create_inventory()
        self._create_runtime_env()
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-one-generated",
            scenario_name="cross-site-zenoh-vm",
        )
        generated_run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=1,
            completed_runs=0,
            failed_runs=0,
            compiled_payload_snapshot={
                "execution_policy": {
                    "stop_on_failure": True,
                    "connectivity_precheck": False,
                },
                "source_snapshots": {
                    "scenario": {
                        "spec": {
                            "nodes": [
                                {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                                {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
                            ],
                        }
                    },
                    "experiment": {"name": "exp-one-generated"},
                },
                "runs": [
                    {
                        "run_id": "run-001",
                        "runtime_env_name": "zenoh-unit8-vm",
                        "phases": [
                            {
                                "name": "preflight",
                                "mode": "sequential",
                                "actions": [
                                    {"type": "check_connectivity", "target": "cloud_vm_bw"},
                                    {"type": "check_connectivity", "target": "vm_home"},
                                ],
                            }
                        ],
                    }
                ],
            },
            results=[],
        )

        result = execute_generated_plan_run_task.run(generated_run_id=generated_run.id)
        self.assertTrue(result["ok"])
        for vm_name in ("vm_home", "cloud_vm_bw"):
            vm = VM.objects.get(created_by=self.user, name=vm_name)
            self.assertEqual(vm.status, VM.VMStatus.ACTIVE)
            self.assertEqual(vm.container_runtime_ready, True)
            self.assertEqual(vm.container_runtime_type, "docker")
            self.assertIsNotNone(vm.last_connection_time)
            last_check = (vm.metadata or {}).get("last_connectivity_check") or {}
            self.assertEqual(last_check.get("ok"), True)
            self.assertEqual(last_check.get("run_id"), "run-001")
            self.assertEqual(last_check.get("check_container_runtime"), True)

    @patch("profiling_exps.celery_tasks._execute_one_action")
    def test_generated_task_reuses_runtime_check_for_later_prechecks(self, execute_one_action_mock):
        from profiling_exps.api import execute_generated_plan_run_task

        precheck_runtime_flags: list[bool] = []

        def _fake_execute_one_action(**kwargs):
            phase = kwargs.get("phase") if isinstance(kwargs.get("phase"), dict) else {}
            action = kwargs.get("action") if isinstance(kwargs.get("action"), dict) else {}
            action_type = str(action.get("type") or "")
            params = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
            if str(phase.get("name") or "") == "__precheck_connectivity__" and action_type == "check_connectivity":
                precheck_runtime_flags.append(bool(params.get("check_container_runtime", True)))
            return {
                "type": action_type,
                "target": str(action.get("target") or ""),
                "status": "SUCCESS",
                "success": True,
                "message": "ok",
                "debug": {"returncode": 0, "stderr": ""},
            }

        execute_one_action_mock.side_effect = _fake_execute_one_action

        self._create_inventory()
        self._create_runtime_env()
        experiment = ProfilingExperiment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="exp-precheck-cache",
            scenario_name="cross-site-zenoh-vm",
        )
        base_run = {
            "runtime_env_name": "zenoh-unit8-vm",
            "phases": [
                {
                    "name": "setup_routing_to_cloud",
                    "mode": "sequential",
                    "actions": [
                        {"type": "run_runtime_preset", "preset": "routing/cloud_router", "target": "cloud_vm_bw"},
                        {"type": "run_runtime_preset", "preset": "routing/local_router", "target": "vm_home"},
                    ],
                }
            ],
        }
        compiled_payload_snapshot = {
            "execution_policy": {
                "stop_on_failure": True,
                "connectivity_precheck": True,
            },
            "source_snapshots": {
                "scenario": {
                    "spec": {
                        "nodes": [
                            {"name": "vm_home", "kind": "vm", "ref": "vm_home"},
                            {"name": "cloud_vm_bw", "kind": "vm", "ref": "cloud_vm_bw"},
                        ],
                    }
                },
                "experiment": {"name": "exp-precheck-cache"},
            },
            "runs": [
                {**deepcopy(base_run), "run_id": "run-001"},
                {**deepcopy(base_run), "run_id": "run-002"},
            ],
        }

        generated_run = ProfilingRun.objects.create(
            experiment=experiment,
            requested_by=self.user,
            status=ProfilingRun.RunStatus.PENDING,
            stop_on_failure=True,
            total_runs=2,
            completed_runs=0,
            failed_runs=0,
            compiled_payload_snapshot=compiled_payload_snapshot,
            results=[],
        )

        result = execute_generated_plan_run_task.run(generated_run_id=generated_run.id)
        self.assertTrue(result["ok"])
        self.assertEqual(len(precheck_runtime_flags), 4)
        self.assertEqual(precheck_runtime_flags[:2], [True, True])
        self.assertEqual(precheck_runtime_flags[2:], [False, False])

    def test_prepare_action_payload_replaces_validate_metrics_dir_by_default(self):
        from profiling_exps.celery_tasks import _prepare_action_payload_for_execution

        prepared = _prepare_action_payload_for_execution(
            action={
                "type": "run_runtime_preset",
                "preset": "profiling/latency_sender",
                "parameters": {
                    "validate_metrics_dir": "/tmp/netanalyzer/validate-old",
                },
            },
            inventory_by_node={},
            run_metrics_remote_dir="/tmp/icopa/generated/single-test/run_1/metrics/run-001",
            run_metrics_backend_dir="static/profiling_exps/single-test/run_1/metrics/run-001",
            run_variant_values=None,
        )
        params = prepared.get("parameters") if isinstance(prepared.get("parameters"), dict) else {}
        self.assertEqual(
            params.get("validate_metrics_dir"),
            "/tmp/icopa/generated/single-test/run_1/metrics/run-001",
        )
        self.assertEqual(
            params.get("validate_metrics_path"),
            "/tmp/icopa/generated/single-test/run_1/metrics/run-001",
        )
        self.assertEqual(
            params.get("icopa_metrics_dir"),
            "/tmp/icopa/generated/single-test/run_1/metrics/run-001",
        )

    def test_prepare_action_payload_adds_rrt_overrides_from_variant_values(self):
        from profiling_exps.celery_tasks import _prepare_action_payload_for_execution

        prepared = _prepare_action_payload_for_execution(
            action={
                "type": "run_runtime_preset",
                "preset": "profiling/latency_sender",
                "parameters": {},
            },
            inventory_by_node={},
            run_metrics_remote_dir="/tmp/icopa/generated/single-test/run_1/metrics/run-001",
            run_metrics_backend_dir="static/profiling_exps/single-test/run_1/metrics/run-001",
            run_variant_values={
                "payload_size": "512KB",
                "frequency_hz": 60,
                "response_payload_size": "128KB",
            },
        )
        params = prepared.get("parameters") if isinstance(prepared.get("parameters"), dict) else {}
        overrides = params.get("rrt_config_overrides") if isinstance(params.get("rrt_config_overrides"), dict) else {}
        self.assertEqual(
            overrides.get("icopa_sender", {}).get("ros__parameters", {}).get("payload_size"),
            "512KB",
        )
        self.assertEqual(
            overrides.get("icopa_sender", {}).get("ros__parameters", {}).get("frequency_hz"),
            60,
        )
        self.assertEqual(
            overrides.get("icopa_responder", {}).get("ros__parameters", {}).get("response_payload_size"),
            "128KB",
        )

    def test_prepare_action_payload_adds_rrt_overrides_from_colon_sender_keys(self):
        from profiling_exps.celery_tasks import _prepare_action_payload_for_execution

        prepared = _prepare_action_payload_for_execution(
            action={
                "type": "run_runtime_preset",
                "preset": "profiling/latency_sender",
                "parameters": {},
            },
            inventory_by_node={},
            run_metrics_remote_dir="/tmp/icopa/generated/single-test/run_1/metrics/run-001",
            run_metrics_backend_dir="static/profiling_exps/single-test/run_1/metrics/run-001",
            run_variant_values={
                "icopa_sender:payload_size": "32KB",
                "icopa_sender:frequency_hz": 60,
                "icopa_responder:response_payload_size": "128KB",
            },
        )
        params = prepared.get("parameters") if isinstance(prepared.get("parameters"), dict) else {}
        overrides = params.get("rrt_config_overrides") if isinstance(params.get("rrt_config_overrides"), dict) else {}
        self.assertEqual(
            overrides.get("icopa_sender", {}).get("ros__parameters", {}).get("payload_size"),
            "32KB",
        )
        self.assertEqual(
            overrides.get("icopa_sender", {}).get("ros__parameters", {}).get("frequency_hz"),
            60,
        )
        self.assertEqual(
            overrides.get("icopa_responder", {}).get("ros__parameters", {}).get("response_payload_size"),
            "128KB",
        )

    def test_build_phase_action_tracker_supports_legacy_steps(self):
        from profiling_exps.celery_tasks import _build_phase_action_tracker

        tracker = _build_phase_action_tracker(
            [
                {
                    "run_id": "run-001",
                    "phases": [
                        {
                            "name": "probe",
                            "steps": [
                                {
                                    "type": "check_connectivity",
                                    "target": "vm_home",
                                }
                            ],
                        }
                    ],
                }
            ]
        )

        run = (tracker.get("runs") or {}).get("run-001")
        self.assertTrue(isinstance(run, dict))
        phase = run["phases"][0]
        self.assertEqual(phase["name"], "probe")
        self.assertEqual(phase["actions"][0]["type"], "check_connectivity")
        self.assertEqual(phase["actions"][0]["target"], "vm_home")

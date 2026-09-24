from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch
from rest_framework import status
from rest_framework.test import APITestCase

from inventory.models import SSHAccessHistory, SSHCredential, VM
from runtime_env.models import RuntimeEnvironment

from icopa_core.task_executor import ActionExecutionResult

from .celery_tasks import validate_scenario_task
from .models import Scenario, ScenarioValidationRun


User = get_user_model()


class ScenarioAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="agent", password="testpass123")
        self.client.force_authenticate(user=self.user)
        credential = SSHCredential.objects.create(
            created_by=self.user,
            name="key1",
            key_path="/tmp/id_rsa",
            user_name="ubuntu",
        )

        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_home",
            address="192.168.1.10",
            user_name="ubuntu",
            credential=credential,
        )
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="cloud_vm_bw",
            address="10.0.0.1",
            user_name="ubuntu",
            credential=credential,
        )
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="ros2-jazzy-zenoh",
            kind="RuntimeEnvironment",
            metadata={"name": "ros2-jazzy-zenoh"},
            command_preset=[
                {"group": "routing", "name": "cloud_router", "executor": "ssh", "command": "echo cloud"},
                {"group": "routing", "name": "local_router", "executor": "ssh", "command": "echo local"},
                {"group": "profiling", "name": "latency_responder", "executor": "ssh", "command": "echo responder"},
                {"group": "profiling", "name": "latency_sender", "executor": "ssh", "command": "echo sender"},
                {"group": "stress", "name": "stress_cpu_ram", "executor": "ssh", "command": "echo stress"},
            ],
        )

    def _upload(self, content: bytes, force: bool = False):
        upload = SimpleUploadedFile("scenario.yaml", content, content_type="application/x-yaml")
        return self.client.post(
            reverse("scenario_list_upload"),
            data={"file": upload, "force": force},
            format="multipart",
        )

    def test_upload_scenario_creates_entry(self):
        content = b"""
kind: Scenario
metadata:
  name: scenario-001
  description: Basic test scenario
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  payloads:
    - name: rgbd-small
      type: bytes
  backgroundWorkloads: []
"""
        response = self._upload(content)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-001")
        self.assertEqual(scenario.kind, "Scenario")
        self.assertEqual(scenario.nodes[0]["nodeName"], "vm_home")
        self.assertEqual(scenario.runtime_env[0]["name"], "ros2-jazzy-zenoh")
        self.assertIn("kind: Scenario", scenario.raw_yaml)

    def test_upload_scenario_accepts_name_nodes_graph_link_list_and_runtime_env_ref(self):
        content = b"""
kind: Scenario
metadata:
  name: cross-site-zenoh-vm
  description: Two-VM cross-site experiment
spec:
  nodes:
    - name: cloud_vm_bw
      kind: vm
      profiling_role: responder
      labels: { site: cloud }
    - name: vm_home
      kind: vm
      profiling_role: sender
      labels: { site: local }
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
        link: ["wan", "wifi"]
        type: robot2cloud
  runtimeEnvRef: ros2-jazzy-zenoh
  phaseTemplates:
    - name: probe
      actions:
        - type: run_runtime_preset
          preset: profiling/latency_sender
          targetRef:
            kind: vm
            name: vm_home
"""
        response = self._upload(content)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        scenario = Scenario.objects.get(created_by=self.user, name="cross-site-zenoh-vm")
        self.assertEqual(scenario.nodes[0]["name"], "cloud_vm_bw")
        self.assertEqual(scenario.nodes[0]["profiling_role"], "responder")
        self.assertEqual(scenario.graph["edges"][0]["link"], ["wan", "wifi"])
        self.assertEqual(scenario.graph["edges"][0]["type"], "robot2cloud")
        self.assertEqual(scenario.runtime_env[0]["name"], "ros2-jazzy-zenoh")

    def test_upload_scenario_requires_existing_inventory_vm(self):
        content = b"""
metadata:
  name: scenario-bad-node
spec:
  nodes:
    - nodeName: not-found
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        response = self._upload(content)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Inventory VM 'not-found'", str(response.json()))

    def test_upload_scenario_requires_existing_runtime_env(self):
        content = b"""
metadata:
  name: scenario-bad-runtime
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: not-found
"""
        response = self._upload(content)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Runtime environment 'not-found'", str(response.json()))

    def test_upload_scenario_rejects_deprecated_action_catalog(self):
        content = b"""
metadata:
  name: scenario-deprecated-action-catalog
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  actionCatalog:
    - type: check_connectivity
      requiredFields: [target]
  phaseTemplates:
    - name: preflight
      actions:
        - type: check_connectivity
          targetRef: vm:vm_home
"""
        response = self._upload(content)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("actionCatalog", str(response.json()))

    def test_validate_rejects_legacy_action_fields(self):
        content = b"""
metadata:
  name: scenario-legacy-action-fields
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: preflight
      actions:
        - execAction: wait
          parameters:
            seconds: 1
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-legacy-action-fields")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="preflight",
        )
        self.assertFalse(result["ok"])
        action_errors = result["checks"]["actions"]["errors"]
        self.assertTrue(any("execAction is not supported" in str(item) for item in action_errors))

    def test_validate_rejects_legacy_phase_steps(self):
        content = b"""
metadata:
  name: scenario-legacy-steps
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: preflight
      steps:
        - type: wait
          parameters:
            seconds: 1
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-legacy-steps")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="preflight",
        )
        self.assertFalse(result["ok"])
        action_errors = result["checks"]["actions"]["errors"]
        self.assertTrue(any(".steps is not supported" in str(item) for item in action_errors))

    def test_validate_rejects_legacy_target_shorthand(self):
        content = b"""
metadata:
  name: scenario-legacy-target
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: preflight
      actions:
        - type: check_connectivity
          target: vm_home
          parameters: {}
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-legacy-target")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="preflight",
        )
        self.assertFalse(result["ok"])
        action_errors = result["checks"]["actions"]["errors"]
        self.assertTrue(any(".target is not supported" in str(item) for item in action_errors))

    def test_scenario_detail_and_delete(self):
        content = b"""
metadata:
  name: scenario-detail
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)

        detail_resp = self.client.get(reverse("scenario_detail", kwargs={"scenario_name": "scenario-detail"}))
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.json()["name"], "scenario-detail")

        delete_resp = self.client.delete(reverse("scenario_detail", kwargs={"scenario_name": "scenario-detail"}))
        self.assertEqual(delete_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Scenario.objects.filter(created_by=self.user, name="scenario-detail").exists())

    def test_scenario_detail_includes_graph_edge_summary_fields(self):
        content = b"""
metadata:
  name: scenario-detail-graph-summary
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_cloud
        from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)

        detail_resp = self.client.get(
            reverse("scenario_detail", kwargs={"scenario_name": "scenario-detail-graph-summary"})
        )
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        body = detail_resp.json()
        self.assertEqual(body["graph_edge_count"], 1)
        self.assertEqual(body["graph_edges"][0]["name"], "robot_to_cloud")
        self.assertEqual(body["graph_edges"][0]["from"], "vm_home")
        self.assertEqual(body["graph_edges"][0]["to"], "cloud_vm_bw")
        self.assertEqual(body["last_graph_check_summary"]["total_edges"], 1)
        self.assertIn("last_failure_report", body)
        self.assertFalse(body["last_failure_report"]["has_failure"])

    def test_scenario_detail_keeps_last_node_summary_after_graph_only_run(self):
        content = b"""
metadata:
  name: scenario-detail-node-summary-fallback
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_cloud
        from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-detail-node-summary-fallback")

        node_run_payload = {
            "check_nodes": True,
            "checks": {
                "nodes": {
                    "ok": True,
                    "checked_nodes": 2,
                    "results": [
                        {"node": "vm_home", "ok": True, "status": "SUCCESS", "message": "ok"},
                        {"node": "cloud_vm_bw", "ok": True, "status": "SUCCESS", "message": "ok"},
                    ],
                }
            },
        }
        ScenarioValidationRun.objects.create(
            scenario=scenario,
            requested_by=self.user,
            task_id="task-node-summary",
            check_actions=False,
            check_nodes=True,
            check_graph=False,
            phase_name="",
            status=ScenarioValidationRun.RunStatus.SUCCEEDED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            result_data=node_run_payload,
        )
        scenario.check_status_node = Scenario.CheckStatus.PASS
        scenario.last_validation_data = {
            "check_actions": False,
            "check_nodes": False,
            "check_graph": True,
            "checks": {
                "nodes": {"ok": True, "skipped": True, "results": []},
                "graph": {"ok": True, "checked_edges": 1, "results": []},
            },
        }
        scenario.save(update_fields=["check_status_node", "last_validation_data", "updated_at"])

        detail_resp = self.client.get(
            reverse("scenario_detail", kwargs={"scenario_name": "scenario-detail-node-summary-fallback"})
        )
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        body = detail_resp.json()
        node_summary = body.get("last_node_check_summary") or {}
        self.assertEqual(node_summary.get("status"), "PASS")
        self.assertEqual(node_summary.get("checked_nodes"), 2)
        self.assertEqual(node_summary.get("passed_nodes"), 2)
        self.assertEqual(node_summary.get("total_nodes"), 2)
        rows = node_summary.get("results") if isinstance(node_summary.get("results"), list) else []
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].get("node"), "vm_home")
        self.assertTrue(str(node_summary.get("checked_at") or "").strip())

    def test_upload_existing_scenario_returns_updated_fields(self):
        first = b"""
metadata:
  name: scenario-update
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  payloads:
    - name: rgbd-small
"""
        second = b"""
metadata:
  name: scenario-update
  description: Updated
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  payloads:
    - name: pointcloud-mid
"""
        self._upload(first)
        response = self._upload(second, force=True)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["updated"]["scenarios"][0]["name"], "scenario-update")
        self.assertEqual(
            body["updated"]["scenarios"][0]["updated_fields"]["description"],
            {"old": "", "new": "Updated"},
        )

    def test_upload_existing_scenario_without_force_is_rejected(self):
        first = b"""
metadata:
  name: scenario-no-force
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        second = b"""
metadata:
  name: scenario-no-force
  description: Updated
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(first)
        response = self._upload(second, force=False)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already exists", str(response.json()))

    def test_compute_validation_status_for_partial_checks(self):
        content = b"""
metadata:
  name: scenario-partial-status
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-partial-status")
        scenario.check_status_actions = Scenario.CheckStatus.UNKNOWN
        scenario.check_status_node = Scenario.CheckStatus.PASS
        scenario.check_status_graph = Scenario.CheckStatus.PASS
        self.assertEqual(scenario.compute_validation_status(), Scenario.ValidationStatus.IDLE)

    def test_upload_runtime_environment_yaml_as_scenario_is_rejected(self):
        content = b"""
kind: RuntimeEnvironment
metadata:
  name: ros2-jazzy-zenoh-unit8-vm
spec:
  images:
    runnerImage: example.invalid/icopa/zenoh_jazzy_ci:v2
"""
        response = self._upload(content)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Expected kind 'Scenario'", str(response.json()))

    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_scenario_actions_with_phase_filter(self, apply_async_mock):
        content = b"""
metadata:
  name: scenario-validate-phase
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: preflight
      actions:
        - type: check_connectivity
          targetRef: vm:vm_home
    - name: probe
      actions:
        - type: run_runtime_preset
          targetRef: vm:cloud_vm_bw
          preset: routing/cloud_router
"""
        self._upload(content)
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-123"})()
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-validate-phase"}),
            data={"phase_name": "preflight", "check_nodes": False, "check_graph": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["validation_status"], "PENDING")
        self.assertEqual(body["task_id"], "task-123")
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-validate-phase")
        self.assertEqual(scenario.validation_status, Scenario.ValidationStatus.PENDING)
        self.assertEqual(scenario.check_status_actions, Scenario.CheckStatus.PENDING)
        self.assertEqual(scenario.check_status_node, Scenario.CheckStatus.UNKNOWN)
        self.assertEqual(scenario.check_status_graph, Scenario.CheckStatus.UNKNOWN)
        self.assertTrue(scenario.validation_requested)
        self.assertEqual(scenario.last_validation_task_id, "task-123")
        run = ScenarioValidationRun.objects.get(scenario=scenario)
        self.assertEqual(run.status, ScenarioValidationRun.RunStatus.PENDING)
        self.assertEqual(run.task_id, "task-123")
        self.assertEqual(run.check_actions, True)
        self.assertEqual(run.check_nodes, False)
        self.assertEqual(run.check_graph, False)

    @patch("scenarios.api.validate_scenario_task.AsyncResult")
    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_reuses_inflight_same_scope(self, apply_async_mock, async_result_mock):
        content = b"""
metadata:
  name: scenario-inflight
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-inflight")
        scenario.validation_requested = True
        scenario.validation_status = Scenario.ValidationStatus.PENDING
        scenario.last_validation_task_id = "task-existing"
        scenario.check_status_actions = Scenario.CheckStatus.PENDING
        scenario.check_status_node = Scenario.CheckStatus.PENDING
        scenario.check_status_graph = Scenario.CheckStatus.SKIPPED
        scenario.last_validation_data = {
            "queued_at": timezone.now().isoformat(),
            "check_nodes": True,
            "check_graph": False,
            "check_actions": False,
            "phase_name": None,
            "task_id": "task-existing",
            "status": "PENDING",
        }
        scenario.save()
        ScenarioValidationRun.objects.create(
            scenario=scenario,
            requested_by=self.user,
            task_id="task-existing",
            check_actions=False,
            check_nodes=True,
            check_graph=False,
            phase_name="",
            status=ScenarioValidationRun.RunStatus.PENDING,
            result_data=scenario.last_validation_data,
        )
        async_result_mock.return_value = type("AsyncMeta", (), {"state": "STARTED"})()

        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-inflight"}),
            data={"check_actions": False, "check_nodes": True, "check_graph": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["execution_mode"], "queued_existing")
        self.assertEqual(body["task_id"], "task-existing")
        apply_async_mock.assert_not_called()

    @patch("scenarios.api.validate_scenario_task.AsyncResult")
    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_recent_failed_task_is_released_and_requeued(self, apply_async_mock, async_result_mock):
        content = b"""
metadata:
  name: scenario-recent-failed
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-recent-failed")
        scenario.validation_requested = True
        scenario.validation_status = Scenario.ValidationStatus.PENDING
        scenario.last_validation_task_id = "task-failed-fast"
        scenario.save()

        old_run = ScenarioValidationRun.objects.create(
            scenario=scenario,
            requested_by=self.user,
            task_id="task-failed-fast",
            check_actions=False,
            check_nodes=False,
            check_graph=True,
            phase_name="",
            status=ScenarioValidationRun.RunStatus.PENDING,
            result_data={"status": "PENDING"},
        )
        # keep it "recent" to exercise the no-reuse path for failed task state
        old_run.requested_at = timezone.now()
        old_run.save(update_fields=["requested_at"])

        async_result_mock.return_value = type("AsyncMeta", (), {"state": "FAILURE"})()
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-new"})()

        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-recent-failed"}),
            data={"check_actions": False, "check_nodes": False, "check_graph": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["task_id"], "task-new")
        old_run.refresh_from_db()
        self.assertEqual(old_run.status, ScenarioValidationRun.RunStatus.FAILED)
        self.assertEqual(old_run.result_data.get("observed_task_state"), "FAILURE")

    @patch("scenarios.api.validate_scenario_task.apply_async")
    @patch("scenarios.api.validate_scenario_task.AsyncResult")
    def test_validate_releases_stale_pending_task(self, async_result_mock, apply_async_mock):
        content = b"""
metadata:
  name: scenario-stale
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-stale")
        scenario.validation_requested = True
        scenario.validation_status = Scenario.ValidationStatus.PENDING
        scenario.last_validation_task_id = "task-stale"
        scenario.check_status_actions = Scenario.CheckStatus.PENDING
        scenario.check_status_node = Scenario.CheckStatus.PENDING
        scenario.check_status_graph = Scenario.CheckStatus.SKIPPED
        scenario.last_validation_data = {
            "check_actions": False,
            "check_nodes": True,
            "check_graph": False,
            "phase_name": None,
            "task_id": "task-stale",
            "queued_at": "2020-01-01T00:00:00+00:00",
            "status": "PENDING",
        }
        scenario.save()

        async_result_mock.return_value = type("AsyncMeta", (), {"state": "PENDING"})()
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-new"})()

        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-stale"}),
            data={"check_actions": False, "check_nodes": True, "check_graph": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["task_id"], "task-new")
        scenario.refresh_from_db()
        self.assertEqual(scenario.validation_status, Scenario.ValidationStatus.PENDING)
        self.assertEqual(scenario.last_validation_task_id, "task-new")

    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_node_only_preserves_non_requested_statuses(self, apply_async_mock):
        content = b"""
metadata:
  name: scenario-node-only
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-node-only"})()
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-node-only"}),
            data={"check_actions": False, "check_nodes": True, "check_graph": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-node-only")
        self.assertEqual(scenario.check_status_actions, Scenario.CheckStatus.UNKNOWN)
        self.assertEqual(scenario.check_status_node, Scenario.CheckStatus.PENDING)
        self.assertEqual(scenario.check_status_graph, Scenario.CheckStatus.UNKNOWN)

    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_graph_only_keeps_previous_node_status(self, apply_async_mock):
        content = b"""
metadata:
  name: scenario-graph-preserve-node
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-graph-preserve-node")
        scenario.check_status_node = Scenario.CheckStatus.PASS
        scenario.check_status_graph = Scenario.CheckStatus.UNKNOWN
        scenario.check_status_actions = Scenario.CheckStatus.UNKNOWN
        scenario.save(update_fields=["check_status_node", "check_status_graph", "check_status_actions"])

        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-graph-preserve-node"})()
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-graph-preserve-node"}),
            data={"check_actions": False, "check_nodes": False, "check_graph": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        scenario.refresh_from_db()
        self.assertEqual(scenario.check_status_node, Scenario.CheckStatus.PASS)
        self.assertEqual(scenario.check_status_graph, Scenario.CheckStatus.PENDING)

    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_graph_only_response_includes_graph_summary(self, apply_async_mock):
        content = b"""
metadata:
  name: scenario-graph-only-response
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_cloud
        from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-graph-only"})()
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-graph-only-response"}),
            data={"check_actions": False, "check_nodes": False, "check_graph": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["graph_edge_count"], 1)
        summary = body["graph_check_summary"]
        self.assertEqual(summary["total_edges"], 1)
        self.assertEqual(summary["checked_edges"], 0)
        self.assertTrue(summary["requested"])
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-graph-only-response")
        self.assertEqual(scenario.last_validation_data["graph_check_summary"]["total_edges"], 1)

    @patch("scenarios.api.validate_scenario_task.apply_async")
    def test_validate_graph_by_edge_name_queues_edge_only_task(self, apply_async_mock):
        credential = SSHCredential.objects.filter(created_by=self.user).first()
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_lab_edge",
            address="192.168.1.11",
            user_name="ubuntu",
            credential=credential,
        )
        content = b"""
metadata:
  name: scenario-graph-edge-select
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: vm_lab_edge
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_edge
        from: vm_home
        to: vm_lab_edge
      - name: edge_to_cloud
        from: vm_lab_edge
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        apply_async_mock.return_value = type("AsyncResult", (), {"id": "task-graph-edge-select"})()
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-graph-edge-select"}),
            data={"check_actions": False, "check_nodes": False, "check_graph": True, "edge_name": "edge_to_cloud"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["edge_name"], "edge_to_cloud")
        kwargs = apply_async_mock.call_args.kwargs.get("kwargs", {})
        self.assertEqual(kwargs.get("edge_name"), "edge_to_cloud")
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-graph-edge-select")
        self.assertEqual(scenario.last_validation_data.get("edge_name"), "edge_to_cloud")

    def test_validate_graph_by_edge_name_rejects_unknown_edge(self):
        content = b"""
metadata:
  name: scenario-graph-edge-reject
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_cloud
        from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-graph-edge-reject"}),
            data={"check_actions": False, "check_nodes": False, "check_graph": True, "edge_name": "missing_edge"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("edge_name", response.json())

    @patch("scenarios.api.validate_scenario_task.AsyncResult")
    def test_validate_clean_resets_state(self, async_result_mock):
        content = b"""
metadata:
  name: scenario-clean
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-clean")
        scenario.validation_requested = True
        scenario.validation_status = Scenario.ValidationStatus.PENDING
        scenario.last_validation_task_id = "task-clean"
        scenario.check_status_actions = Scenario.CheckStatus.PENDING
        scenario.check_status_node = Scenario.CheckStatus.PENDING
        scenario.check_status_graph = Scenario.CheckStatus.FAIL
        scenario.validation_trace = [{"step": "x"}]
        scenario.validation_history = [{"run": 1}]
        scenario.last_validation_data = {"status": "PENDING"}
        scenario.save()

        async_result_mock.return_value = type("AsyncMeta", (), {"revoke": lambda self, terminate=False: None})()
        response = self.client.post(
            reverse("scenario_validate", kwargs={"scenario_name": "scenario-clean"}),
            data={"clean": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        scenario.refresh_from_db()
        self.assertEqual(scenario.validation_status, Scenario.ValidationStatus.IDLE)
        self.assertFalse(scenario.validation_requested)
        self.assertEqual(scenario.last_validation_task_id, "")
        self.assertEqual(scenario.validation_trace, [])
        self.assertEqual(scenario.validation_history, [])

    @patch("scenarios.celery_tasks.GraphPingCheckExec.execute")
    @patch("scenarios.celery_tasks.VMConnectCheckExec.execute")
    def test_validate_scenario_nodes_and_graph(self, connect_exec_mock, graph_exec_mock):
        content = b"""
metadata:
  name: scenario-validate-connectivity
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: preflight
      actions:
        - type: check_connectivity
          targetRef: vm:vm_home
        - type: check_connectivity
          targetRef: vm:cloud_vm_bw
"""
        self._upload(content)
        connect_exec_mock.return_value = ActionExecutionResult(
            action_type="check_connectivity",
            target="vm_home",
            success=True,
            status="SUCCESS",
            message="ok",
        )
        graph_exec_mock.return_value = ActionExecutionResult(
            action_type="check_graph_ping",
            target="vm_home",
            success=True,
            status="SUCCESS",
            message="ok",
            debug={},
        )

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-validate-connectivity")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_nodes=True,
            check_graph=True,
            phase_name=None,
        )
        self.assertTrue(result["ok"])
        scenario.refresh_from_db()
        self.assertEqual(scenario.validation_status, Scenario.ValidationStatus.SUCCEEDED)
        self.assertEqual(scenario.check_status_actions, Scenario.CheckStatus.PASS)
        self.assertEqual(scenario.check_status_node, Scenario.CheckStatus.PASS)
        self.assertEqual(scenario.check_status_graph, Scenario.CheckStatus.PASS)
        self.assertIsNotNone(scenario.last_validation_at)
        run = ScenarioValidationRun.objects.filter(scenario=scenario).order_by("-requested_at").first()
        self.assertIsNotNone(run)
        self.assertEqual(run.status, ScenarioValidationRun.RunStatus.SUCCEEDED)

    @patch("scenarios.celery_tasks.build_vm_ssh_client")
    @patch("scenarios.celery_tasks.VMConnectCheckExec.execute")
    def test_validate_nodes_fails_when_tracked_managed_container_is_not_running(
        self,
        connect_exec_mock,
        build_vm_ssh_client_mock,
    ):
        class _FakeContainerStatus:
            def __init__(self, *, exists: bool, running: bool, status: str):
                self.exists = exists
                self.running = running
                self.status = status

        class _FakeSSHClient:
            def list_running_containers(self, timeout: int = 20):
                return []

            def get_container_status(self, container_name: str, timeout: int = 20):
                return _FakeContainerStatus(exists=True, running=False, status="Exited (1)")

        vm = VM.objects.get(created_by=self.user, name="vm_home")
        vm.managed_containers = ["icopa-router-home"]
        vm.save(update_fields=["managed_containers", "updated_at"])

        content = b"""
metadata:
  name: scenario-node-managed-missing
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        connect_exec_mock.return_value = ActionExecutionResult(
            action_type="check_connectivity",
            target="vm_home",
            success=True,
            status="SUCCESS",
            message="Connectivity check completed.",
            debug={"returncode": 0, "stderr": ""},
        )
        build_vm_ssh_client_mock.return_value = _FakeSSHClient()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-node-managed-missing")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=False,
            check_nodes=True,
            check_graph=False,
            phase_name=None,
        )

        self.assertFalse(result["ok"])
        node_results = result["checks"]["nodes"]["results"]
        self.assertEqual(len(node_results), 1)
        node_item = node_results[0]
        self.assertEqual(node_item.get("node"), "vm_home")
        self.assertFalse(node_item.get("ok"))
        self.assertFalse(node_item.get("managed_containers_ok"))
        self.assertEqual(node_item.get("managed_containers"), ["icopa-router-home"])
        status_rows = node_item.get("managed_container_status") or []
        self.assertEqual(len(status_rows), 1)
        self.assertEqual(status_rows[0].get("state"), "stopped")

    @patch("scenarios.celery_tasks.build_vm_ssh_client")
    @patch("scenarios.celery_tasks.VMConnectCheckExec.execute")
    def test_validate_nodes_passes_when_tracked_managed_container_is_running(
        self,
        connect_exec_mock,
        build_vm_ssh_client_mock,
    ):
        class _FakeSSHClient:
            def list_running_containers(self, timeout: int = 20):
                return [{"name": "icopa-router-home", "status": "Up 5 minutes"}]

            def get_container_status(self, container_name: str, timeout: int = 20):
                raise AssertionError("get_container_status should not be called for running container")

        vm = VM.objects.get(created_by=self.user, name="vm_home")
        vm.managed_containers = ["icopa-router-home"]
        vm.save(update_fields=["managed_containers", "updated_at"])

        content = b"""
metadata:
  name: scenario-node-managed-running
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        connect_exec_mock.return_value = ActionExecutionResult(
            action_type="check_connectivity",
            target="vm_home",
            success=True,
            status="SUCCESS",
            message="Connectivity check completed.",
            debug={"returncode": 0, "stderr": ""},
        )
        build_vm_ssh_client_mock.return_value = _FakeSSHClient()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-node-managed-running")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=False,
            check_nodes=True,
            check_graph=False,
            phase_name=None,
        )

        self.assertTrue(result["ok"])
        node_results = result["checks"]["nodes"]["results"]
        self.assertEqual(len(node_results), 1)
        node_item = node_results[0]
        self.assertTrue(node_item.get("ok"))
        self.assertTrue(node_item.get("managed_containers_ok"))
        self.assertEqual(node_item.get("managed_containers_running"), ["icopa-router-home"])

    @patch("scenarios.celery_tasks.GraphPingCheckExec.execute")
    def test_validate_scenario_graph_checks_each_edge(self, graph_exec_mock):
        credential = SSHCredential.objects.filter(created_by=self.user).first()
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_lab_edge",
            address="192.168.1.11",
            user_name="ubuntu",
            credential=credential,
        )
        content = b"""
metadata:
  name: scenario-validate-multi-edge-graph
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: vm_lab_edge
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_edge
        from: vm_home
        to: vm_lab_edge
      - name: edge_to_cloud
        from: vm_lab_edge
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        graph_exec_mock.return_value = ActionExecutionResult(
            action_type="check_graph_ping",
            target="vm_home",
            success=True,
            status="SUCCESS",
            message="ok",
            debug={},
            metrics={"latency_avg_ms": 12.3},
        )

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-validate-multi-edge-graph")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=False,
            check_nodes=False,
            check_graph=True,
            phase_name=None,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(graph_exec_mock.call_count, 2)
        graph_payload = result["checks"]["graph"]
        self.assertEqual(graph_payload.get("checked_edges"), 2)
        self.assertEqual(graph_payload.get("summary", {}).get("total_edges"), 2)
        self.assertEqual(result.get("graph_check_summary", {}).get("total_edges"), 2)
        graph_results = graph_payload.get("results")
        self.assertTrue(isinstance(graph_results, list))
        self.assertEqual(len(graph_results), 2)
        edge_names = {str(item.get("edge_name") or "") for item in graph_results if isinstance(item, dict)}
        self.assertEqual(edge_names, {"robot_to_edge", "edge_to_cloud"})

    @patch("scenarios.celery_tasks.GraphPingCheckExec.execute")
    def test_validate_scenario_graph_checks_selected_edge_only(self, graph_exec_mock):
        credential = SSHCredential.objects.filter(created_by=self.user).first()
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_lab_edge",
            address="192.168.1.11",
            user_name="ubuntu",
            credential=credential,
        )
        content = b"""
metadata:
  name: scenario-validate-selected-edge-only
spec:
  nodes:
    - nodeName: vm_home
      kind: vm
    - nodeName: vm_lab_edge
      kind: vm
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges:
      - name: robot_to_edge
        from: vm_home
        to: vm_lab_edge
      - name: edge_to_cloud
        from: vm_lab_edge
        to: cloud_vm_bw
  runtimeEnv:
    - name: ros2-jazzy-zenoh
"""
        self._upload(content)
        graph_exec_mock.return_value = ActionExecutionResult(
            action_type="check_graph_ping",
            target="vm_home",
            success=True,
            status="SUCCESS",
            message="ok",
            debug={},
            metrics={"latency_avg_ms": 11.1},
        )

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-validate-selected-edge-only")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=False,
            check_nodes=False,
            check_graph=True,
            edge_name="edge_to_cloud",
            phase_name=None,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(graph_exec_mock.call_count, 1)
        graph_payload = result["checks"]["graph"]
        self.assertEqual(graph_payload.get("checked_edges"), 1)
        self.assertEqual(graph_payload.get("edge_name"), "edge_to_cloud")
        graph_results = graph_payload.get("results")
        self.assertTrue(isinstance(graph_results, list))
        self.assertEqual(len(graph_results), 1)
        self.assertEqual(str(graph_results[0].get("edge_name") or ""), "edge_to_cloud")

    @patch("scenarios.celery_tasks.time.sleep")
    @patch("scenarios.celery_tasks.build_executor")
    def test_validate_phase_executes_routing_actions(self, build_executor_mock, sleep_mock):
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm",
            kind="RuntimeEnvironment",
            metadata={"name": "zenoh-unit8-vm"},
            tags={"zenoh": {"enabled": True, "routerPort": 7447}},
            command_preset=[
                {"group": "routing", "name": "cloud_router", "executor": "ssh", "command": "echo cloud"},
                {"group": "routing", "name": "local_router", "executor": "ssh", "command": "echo local"},
            ],
        )
        content = b"""
metadata:
  name: scenario-phase-exec
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
    - nodeName: vm_home
      kind: vm
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnv:
    - name: zenoh-unit8-vm
  phaseTemplates:
    - name: setup_routing_to_cloud
      actions:
        - type: run_runtime_preset
          preset: cloud_router
          targetRef: vm:cloud_vm_bw
        - type: wait
          parameters:
            seconds: 3
        - type: run_runtime_preset
          preset: local_router
          targetRef: vm:vm_home
        - type: wait
          parameters:
            seconds: 3
"""
        self._upload(content)

        class _FakeExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset:routing:zenoh",
                    target="vm",
                    success=True,
                    status="SUCCESS",
                    message="ok",
                    debug={},
                )

        build_executor_mock.return_value = _FakeExec()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-phase-exec")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_routing_to_cloud",
        )
        self.assertTrue(result["ok"])
        actions_check = result["checks"]["actions"]
        self.assertTrue(actions_check["ok"])
        self.assertEqual(actions_check["execution"]["executed_actions"], 4)
        self.assertEqual(build_executor_mock.call_count, 2)
        sleep_mock.assert_any_call(3.0)

    @patch("scenarios.celery_tasks.time.sleep")
    @patch("scenarios.celery_tasks.build_executor")
    def test_validate_phase_executes_canonical_target_ref_actions(self, build_executor_mock, sleep_mock):
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm",
            kind="RuntimeEnvironment",
            metadata={"name": "zenoh-unit8-vm"},
            tags={"zenoh": {"enabled": True, "routerPort": 7447}},
            command_preset=[
                {"group": "profiling", "name": "latency_responder", "executor": "ssh", "command": "echo responder"},
                {"group": "profiling", "name": "latency_sender", "executor": "ssh", "command": "echo sender"},
            ],
        )
        content = b"""
metadata:
  name: scenario-phase-exec-shorthand
spec:
  nodes:
    - name: cloud_vm_bw
      kind: vm
      labels: { site: cloud }
    - name: vm_home
      kind: vm
      labels: { site: local }
  graph:
    edges:
      - from: vm_home
        to: cloud_vm_bw
  runtimeEnvRef: zenoh-unit8-vm
  phaseTemplates:
    - name: setup_routing_to_cloud
      mode: sequential
      actions:
        - type: run_runtime_preset
          targetRef: vm:cloud_vm_bw
          preset: routing/setup_zenoh_routing
          parameters:
            preset_container_id: zenoh_router:jazzy_v1
            exec_cmd_type: vm_ce_router_exec_cmd
        - type: wait
          parameters:
            seconds: 3
        - type: run_runtime_preset
          targetRef: vm:vm_home
          preset: routing/setup_zenoh_routing/jazzy_v1
          parameters:
            preset_container_id: zenoh_router:jazzy_v1
            exec_cmd_type: vm_local_router_exec_cmd
"""
        self._upload(content)

        class _FakeExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset:routing:zenoh",
                    target="vm",
                    success=True,
                    status="SUCCESS",
                    message="ok",
                    debug={},
                )

        build_executor_mock.return_value = _FakeExec()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-phase-exec-shorthand")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_routing_to_cloud",
        )
        self.assertTrue(result["ok"])
        actions_check = result["checks"]["actions"]
        self.assertTrue(actions_check["ok"])
        self.assertEqual(actions_check["execution"]["executed_actions"], 3)
        self.assertEqual(build_executor_mock.call_count, 2)
        sleep_mock.assert_any_call(3.0)

    @patch("scenarios.celery_tasks.time.sleep")
    @patch("scenarios.celery_tasks.build_executor")
    def test_validate_phase_with_edge_scope_resolves_edge_placeholders_only_for_selected_phase(
        self,
        build_executor_mock,
        sleep_mock,
    ):
        credential = SSHCredential.objects.filter(created_by=self.user).first()
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_home_edge",
            address="192.168.1.221",
            user_name="ubuntu",
            credential=credential,
        )
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="home_nuc_05",
            address="192.168.1.225",
            user_name="ubuntu",
            credential=credential,
        )
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm",
            kind="RuntimeEnvironment",
            metadata={"name": "zenoh-unit8-vm"},
            tags={"zenoh": {"enabled": True, "routerPort": 7447}},
            command_preset=[
                {"group": "routing", "name": "cloud_router", "executor": "ssh", "command": "echo cloud"},
                {"group": "routing", "name": "local_router", "executor": "ssh", "command": "echo local"},
            ],
        )
        content = b"""
metadata:
  name: scenario-phase-edge-scope
spec:
  nodes:
    - name: cloud_vm_bw
      kind: vm
    - name: vm_home_edge
      kind: vm
    - name: home_nuc_05
      kind: vm
  graph:
    edges:
      - name: robot_to_edge
        from: home_nuc_05
        to: vm_home_edge
  runtimeEnvRef: zenoh-unit8-vm
  phaseTemplates:
    - name: setup_stress_workload
      mode: parallel
      actions:
        - type: run_runtime_preset
          targetRef: vm:vm_lab_edge
          preset: stress/stress_cpu_ram
    - name: setup_routing
      mode: sequential
      actions:
        - type: run_runtime_preset
          targetRef: vm:${edge.to}
          preset: routing/setup_zenoh_routing
          parameters:
            preset_container_id: zenoh_router:jazzy_v1
            exec_cmd_type: vm_ce_router_exec_cmd
        - type: wait
          parameters:
            seconds: 1
        - type: run_runtime_preset
          targetRef: vm:${edge.from}
          preset: routing/setup_zenoh_routing/jazzy_v1
          parameters:
            preset_container_id: zenoh_router:jazzy_v1
            exec_cmd_type: vm_local_router_exec_cmd
            peer_node: ${edge.to}
"""
        self._upload(content, force=True)

        class _FakeExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset:routing:zenoh",
                    target="vm",
                    success=True,
                    status="SUCCESS",
                    message="ok",
                    debug={},
                )

        build_executor_mock.return_value = _FakeExec()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-phase-edge-scope")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_routing",
            edge_name="robot_to_edge",
        )

        self.assertTrue(result["ok"])
        actions_check = result["checks"]["actions"]
        self.assertTrue(actions_check["ok"])
        self.assertEqual(actions_check["checked_phases"], ["setup_routing"])
        self.assertEqual(actions_check["execution"]["executed_actions"], 3)
        self.assertEqual(build_executor_mock.call_count, 2)
        sleep_mock.assert_any_call(1.0)

        first_ctx = build_executor_mock.call_args_list[0].args[0]
        second_ctx = build_executor_mock.call_args_list[1].args[0]
        self.assertEqual(first_ctx.action["target"], "vm_home_edge")
        self.assertEqual(second_ctx.action["target"], "home_nuc_05")
        self.assertEqual(second_ctx.action["parameters"]["peer_node"], "vm_home_edge")

    @patch("scenarios.celery_tasks.VMConnectCheckExec.execute")
    def test_validate_phase_node_check_with_edge_scope_resolves_edge_targets(self, connect_exec_mock):
        credential = SSHCredential.objects.filter(created_by=self.user).first()
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="vm_home_edge",
            address="192.168.1.221",
            user_name="ubuntu",
            credential=credential,
        )
        VM.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="home_nuc_05",
            address="192.168.1.225",
            user_name="ubuntu",
            credential=credential,
        )
        RuntimeEnvironment.objects.create(
            created_by=self.user,
            modified_by=self.user,
            name="zenoh-unit8-vm-node-check",
            kind="RuntimeEnvironment",
            metadata={"name": "zenoh-unit8-vm-node-check"},
            tags={"zenoh": {"enabled": True, "routerPort": 7447}},
            command_preset=[
                {"group": "routing", "name": "cloud_router", "executor": "ssh", "command": "echo cloud"},
                {"group": "routing", "name": "local_router", "executor": "ssh", "command": "echo local"},
            ],
        )
        content = b"""
metadata:
  name: scenario-phase-edge-node-check
spec:
  nodes:
    - name: cloud_vm_bw
      kind: vm
    - name: vm_home_edge
      kind: vm
    - name: home_nuc_05
      kind: vm
  graph:
    edges:
      - name: robot_to_edge
        from: home_nuc_05
        to: vm_home_edge
  runtimeEnvRef: zenoh-unit8-vm-node-check
  phaseTemplates:
    - name: setup_routing
      mode: sequential
      actions:
        - type: run_runtime_preset
          targetRef: vm:${edge.to}
          preset: routing/setup_zenoh_routing
        - type: run_runtime_preset
          targetRef: vm:${edge.from}
          preset: routing/setup_zenoh_routing/jazzy_v1
"""
        self._upload(content, force=True)

        connect_exec_mock.side_effect = [
            ActionExecutionResult(
                action_type="check_connectivity",
                target="vm_home_edge",
                success=True,
                status="SUCCESS",
                message="ok",
                debug={},
            ),
            ActionExecutionResult(
                action_type="check_connectivity",
                target="home_nuc_05",
                success=True,
                status="SUCCESS",
                message="ok",
                debug={},
            ),
        ]

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-phase-edge-node-check")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=False,
            check_nodes=True,
            check_graph=False,
            phase_name="setup_routing",
            edge_name="robot_to_edge",
        )

        self.assertTrue(result["ok"])
        node_payload = result["checks"]["nodes"]
        self.assertTrue(node_payload["ok"])
        self.assertEqual(connect_exec_mock.call_count, 2)
        checked_nodes = {str(item.get("node") or "") for item in node_payload.get("results") or []}
        self.assertEqual(checked_nodes, {"vm_home_edge", "home_nuc_05"})

    @patch("scenarios.celery_tasks.build_executor")
    @patch("scenarios.celery_tasks.build_vm_ssh_client")
    def test_stress_phase_collects_and_caches_vm_capabilities(self, vm_ssh_client_mock, build_executor_mock):
        class _FakeSSHResult:
            def __init__(self, success: bool, stdout: str = "", stderr: str = "", returncode: int = 0):
                self.success = success
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = returncode

        class _FakeSSHClient:
            def __init__(self, *args, **kwargs):
                pass

            def execute_command(self, command: str, timeout: int = 20):
                if command == "nproc":
                    return _FakeSSHResult(True, "8\n")
                if "MemTotal" in command:
                    return _FakeSSHResult(True, "16777216\n")
                if "nvidia-smi" in command:
                    return _FakeSSHResult(True, "")
                if "lspci" in command:
                    return _FakeSSHResult(True, "00:02.0 VGA compatible controller: Fake GPU\n")
                return _FakeSSHResult(True, "")

        vm_ssh_client_mock.side_effect = _FakeSSHClient

        class _FakeExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset",
                    target="cloud_vm_bw",
                    success=True,
                    status="SUCCESS",
                    message="ok",
                    debug={},
                )

        build_executor_mock.return_value = _FakeExec()

        content = b"""
metadata:
  name: scenario-stress-capabilities
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: setup_stress_workload
      actions:
        - type: run_runtime_preset
          preset: stress/stress_cpu_ram
          targetRef: vm:cloud_vm_bw
          parameters:
            cpu_cores: 2
            mem_gb: 1
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-stress-capabilities")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_stress_workload",
        )
        self.assertTrue(result["ok"])
        vm = VM.objects.get(created_by=self.user, name="cloud_vm_bw")
        capabilities = (vm.metadata or {}).get("capabilities") or {}
        self.assertEqual(capabilities.get("cpu_cores"), 8)
        self.assertEqual(capabilities.get("mem_total_gb"), 16.0)
        self.assertEqual(len(capabilities.get("gpu") or []), 1)
        history_rows = SSHAccessHistory.objects.filter(
            requested_by=self.user,
            source="celery_task:stress_capability_probe",
        )
        self.assertGreaterEqual(history_rows.count(), 3)

    @patch("scenarios.celery_tasks.build_executor")
    @patch("scenarios.celery_tasks.build_vm_ssh_client")
    def test_stress_phase_fails_when_requested_resources_exceed_capabilities(
        self, vm_ssh_client_mock, build_executor_mock
    ):
        class _FakeSSHResult:
            def __init__(self, success: bool, stdout: str = "", stderr: str = "", returncode: int = 0):
                self.success = success
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = returncode

        class _FakeSSHClient:
            def __init__(self, *args, **kwargs):
                pass

            def execute_command(self, command: str, timeout: int = 20):
                if command == "nproc":
                    return _FakeSSHResult(True, "2\n")
                if "MemTotal" in command:
                    return _FakeSSHResult(True, "4194304\n")
                if "nvidia-smi" in command:
                    return _FakeSSHResult(True, "")
                if "lspci" in command:
                    return _FakeSSHResult(True, "")
                return _FakeSSHResult(True, "")

        vm_ssh_client_mock.side_effect = _FakeSSHClient
        build_executor_mock.return_value = None

        content = b"""
metadata:
  name: scenario-stress-limit-fail
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: setup_stress_workload
      actions:
        - type: run_runtime_preset
          preset: stress/stress_cpu_ram
          targetRef: vm:cloud_vm_bw
          parameters:
            cpu_cores: 4
            mem_gb: 1
"""
        self._upload(content)
        scenario = Scenario.objects.get(created_by=self.user, name="scenario-stress-limit-fail")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_stress_workload",
        )
        self.assertFalse(result["ok"])
        actions_check = result["checks"]["actions"]
        self.assertFalse(actions_check["ok"])
        self.assertIn("cpu safety limit", " ".join(actions_check["execution"]["errors"]))
        self.assertIn("failure_report", result)
        self.assertTrue(result["failure_report"]["has_failure"])
        self.assertIn("actions", result["failure_report"]["failed_scopes"])
        build_executor_mock.assert_not_called()

    @patch("scenarios.celery_tasks.build_executor")
    def test_phase_action_tracks_container_name_and_cleanup_in_vm_state(self, build_executor_mock):
        content = b"""
metadata:
  name: scenario-container-tracking
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: setup_and_cleanup
      actions:
        - type: run_runtime_preset
          preset: routing/cloud_router
          targetRef: vm:cloud_vm_bw
        - type: cleanup_zenoh_routers
          targetRef: vm:cloud_vm_bw
"""
        self._upload(content)

        class _StartExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset",
                    target="cloud_vm_bw",
                    success=True,
                    status="SUCCESS",
                    message="started",
                    debug={
                        "command": "docker run -d --name icopa-test-container docker.io/example:latest",
                        "returncode": 0,
                    },
                )

        class _CleanupExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="cleanup_zenoh_routers",
                    target="cloud_vm_bw",
                    success=True,
                    status="SUCCESS",
                    message="cleaned",
                    debug={
                        "command": "docker rm -f icopa-test-container >/dev/null 2>&1 || true",
                        "returncode": 0,
                    },
                )

        build_executor_mock.side_effect = [_StartExec(), _CleanupExec()]

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-container-tracking")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_and_cleanup",
        )
        self.assertTrue(result["ok"])
        vm = VM.objects.get(created_by=self.user, name="cloud_vm_bw")
        self.assertEqual(vm.managed_containers, [])
        history_rows = SSHAccessHistory.objects.filter(
            requested_by=self.user,
            source="celery_task:action_outcome",
            scenario_name="scenario-container-tracking",
            phase_name="setup_and_cleanup",
        )
        self.assertEqual(history_rows.count(), 2)

    @patch("scenarios.celery_tasks.build_executor")
    def test_failed_action_does_not_add_managed_container_to_vm_state(self, build_executor_mock):
        content = b"""
metadata:
  name: scenario-container-failed-start
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: setup_failed_container
      actions:
        - type: run_runtime_preset
          preset: routing/cloud_router
          targetRef: vm:cloud_vm_bw
"""
        self._upload(content)

        class _FailedExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset",
                    target="cloud_vm_bw",
                    success=False,
                    status="FAILED",
                    message="failed-start",
                    debug={
                        "command": "docker run -d --name icopa-failed-container docker.io/example:latest",
                        "returncode": 1,
                    },
                )

        build_executor_mock.return_value = _FailedExec()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-container-failed-start")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_failed_container",
        )
        self.assertFalse(result["ok"])
        vm = VM.objects.get(created_by=self.user, name="cloud_vm_bw")
        self.assertEqual(vm.managed_containers, [])

    @patch("scenarios.celery_tasks.build_executor")
    @patch("scenarios.celery_tasks.build_vm_ssh_client")
    def test_success_action_with_rm_then_run_keeps_container_in_vm_state(
        self,
        vm_ssh_client_mock,
        build_executor_mock,
    ):
        content = b"""
metadata:
  name: scenario-container-rm-run
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: setup_rm_run
      actions:
        - type: run_runtime_preset
          preset: stress/stress_cpu_ram
          targetRef: vm:cloud_vm_bw
          parameters:
            cpu_cores: 2
            mem_gb: 1
"""
        self._upload(content)

        class _FakeSSHResult:
            def __init__(self, success: bool, stdout: str = "", stderr: str = "", returncode: int = 0):
                self.success = success
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = returncode

        class _FakeSSHClient:
            def __init__(self, *args, **kwargs):
                pass

            def execute_command(self, command: str, timeout: int = 20):
                if command == "nproc":
                    return _FakeSSHResult(True, "8\n")
                if "MemTotal" in command:
                    return _FakeSSHResult(True, "16777216\n")
                if "nvidia-smi" in command:
                    return _FakeSSHResult(True, "")
                if "lspci" in command:
                    return _FakeSSHResult(True, "")
                return _FakeSSHResult(True, "")

        vm_ssh_client_mock.side_effect = _FakeSSHClient

        class _StressExec:
            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset:stress",
                    target="cloud_vm_bw",
                    success=True,
                    status="SUCCESS",
                    message="started",
                    debug={
                        "command": (
                            "docker rm -f icopa-cross-site-zenoh-vm-run_runtime_preset-cloud_vm_bw "
                            ">/dev/null 2>&1 || true; "
                            "docker run -d --name icopa-cross-site-zenoh-vm-run_runtime_preset-cloud_vm_bw "
                            "example.invalid/icopa/icopa-stress-container:v0.1 --cpu-cores 2 --mem-gb 1"
                        ),
                        "managed_containers_running": [
                            "icopa-cross-site-zenoh-vm-run_runtime_preset-cloud_vm_bw"
                        ],
                        "managed_containers_created": [
                            "icopa-cross-site-zenoh-vm-run_runtime_preset-cloud_vm_bw"
                        ],
                        "returncode": 0,
                    },
                )

        build_executor_mock.return_value = _StressExec()

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-container-rm-run")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="setup_rm_run",
        )
        self.assertTrue(result["ok"])
        vm = VM.objects.get(created_by=self.user, name="cloud_vm_bw")
        self.assertEqual(
            vm.managed_containers,
            ["icopa-cross-site-zenoh-vm-run_runtime_preset-cloud_vm_bw"],
        )

    @patch("scenarios.celery_tasks.VMScpCollectMetricsExec")
    @patch("scenarios.celery_tasks.build_executor")
    def test_probe_then_collect_metrics_uses_pending_run_and_persists_in_run(self, build_executor_mock, collect_exec_cls_mock):
        runtime_env = RuntimeEnvironment.objects.get(created_by=self.user, name="ros2-jazzy-zenoh")
        runtime_env.tags = {"zenoh": {"enabled": True, "routerPort": 7447}}
        runtime_env.command_preset = [
            {"group": "profiling", "name": "latency_responder", "executor": "auto", "command": "echo responder"},
            {"group": "profiling", "name": "latency_sender", "executor": "auto", "command": "echo sender"},
        ]
        runtime_env.save(update_fields=["tags", "command_preset", "updated_at"])

        content = b"""
metadata:
  name: scenario-probe-metrics
spec:
  nodes:
    - nodeName: cloud_vm_bw
      kind: vm
    - nodeName: vm_home
      kind: vm
  graph:
    edges: []
  runtimeEnv:
    - name: ros2-jazzy-zenoh
  phaseTemplates:
    - name: probe
      actions:
        - type: run_runtime_preset
          preset: profiling/latency_responder
          targetRef: vm:cloud_vm_bw
        - type: wait
          parameters:
            seconds: 1
        - type: run_runtime_preset
          preset: profiling/latency_sender
          targetRef: vm:vm_home
    - name: collect_metrics
      actions:
        - type: collect_metrics
          targetRef: vm:vm_home
        - type: collect_metrics
          targetRef: vm:cloud_vm_bw
"""
        self._upload(content)

        captured_validate_dirs: list[str] = []

        class _FakeExec:
            def __init__(self, target: str):
                self.target = target

            def execute(self):
                return ActionExecutionResult(
                    action_type="run_runtime_preset:profiling:zenoh",
                    target=self.target,
                    success=True,
                    status="SUCCESS",
                    message="ok",
                    debug={"command": "docker run -d --name icopa-test", "returncode": 0},
                )

        def _fake_build_executor(ctx):
            parameters = ctx.action.get("parameters") if isinstance(ctx.action, dict) else {}
            if isinstance(parameters, dict):
                captured_validate_dirs.append(str(parameters.get("validate_metrics_dir") or ""))
            return _FakeExec(str(ctx.action.get("target") or ""))

        class _FakeCollectExec:
            def __init__(self, *, targets, remote_dir, local_base_dir, connect_timeout_sec=8, timeout_sec=45):
                self.targets = targets
                self.remote_dir = remote_dir
                self.local_base_dir = local_base_dir
                self.connect_timeout_sec = connect_timeout_sec
                self.timeout_sec = timeout_sec

            def execute(self):
                return {
                    "ok": True,
                    "remote_dir": self.remote_dir,
                    "local_base_dir": self.local_base_dir,
                    "targets": {
                        target.node_name: {
                            "ok": True,
                            "returncode": 0,
                            "stdout": "",
                            "stderr": "",
                            "local_dir": f"{self.local_base_dir}/{target.node_name}",
                            "files": ["rrt_summary.json", "rrt_all.csv"],
                        }
                        for target in self.targets
                    },
                    "errors": [],
                }

        build_executor_mock.side_effect = _fake_build_executor
        collect_exec_cls_mock.side_effect = _FakeCollectExec

        scenario = Scenario.objects.get(created_by=self.user, name="scenario-probe-metrics")
        result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="probe",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(build_executor_mock.call_count, 2)
        self.assertEqual(collect_exec_cls_mock.call_count, 0)
        self.assertEqual(len(captured_validate_dirs), 2)
        self.assertTrue(captured_validate_dirs[0].startswith("/tmp/netanalyzer/validate-"))
        self.assertEqual(captured_validate_dirs[0], captured_validate_dirs[1])

        probe_run = ScenarioValidationRun.objects.filter(scenario=scenario).order_by("-requested_at").first()
        self.assertIsNotNone(probe_run)
        assert probe_run is not None
        self.assertTrue(probe_run.need_to_collect_metrics)
        self.assertEqual(probe_run.probe_metrics.get("folder_name"), captured_validate_dirs[0].split("/")[-1])
        self.assertEqual(probe_run.result_data.get("probe_metrics", {}).get("folder_name"), probe_run.probe_metrics.get("folder_name"))
        self.assertEqual(probe_run.metrics_remote_dir, captured_validate_dirs[0])

        collect_result = validate_scenario_task.run(
            user_id=self.user.id,
            scenario_id=scenario.id,
            check_actions=True,
            check_nodes=False,
            check_graph=False,
            phase_name="collect_metrics",
        )
        self.assertTrue(collect_result["ok"])
        self.assertEqual(collect_exec_cls_mock.call_count, 2)
        self.assertEqual(build_executor_mock.call_count, 2)

        probe_run.refresh_from_db()
        self.assertFalse(probe_run.need_to_collect_metrics)
        self.assertTrue(probe_run.probe_metrics.get("collected"))

        collect_run = ScenarioValidationRun.objects.filter(scenario=scenario).order_by("-requested_at").first()
        self.assertIsNotNone(collect_run)
        assert collect_run is not None
        self.assertEqual(collect_run.phase_name, "collect_metrics")
        self.assertEqual(collect_run.probe_metrics.get("folder_name"), probe_run.probe_metrics.get("folder_name"))
        self.assertFalse(collect_run.need_to_collect_metrics)

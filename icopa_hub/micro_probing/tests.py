from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from inventory.models import VM
from .models import ProbeSession, ProbeWindow
from .tasks import execute_probe_session


METRICS = {"probe_type": "icmp", "reachable": True, "packets_sent": 5, "packets_received": 5,
           "loss_percent": 0, "latency_ms": {"avg": 12.0}, "jitter_ms": 2.0}


class ProbeAPITests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="probe-owner")
        self.other = get_user_model().objects.create_user(username="probe-other")
        self.source = VM.objects.create(name="edge", address="192.0.2.1", created_by=self.user)
        self.target = VM.objects.create(name="cloud", address="192.0.2.2", created_by=self.user)
        self.client.credentials(HTTP_AUTHORIZATION="Token " + Token.objects.create(user=self.user).key)

    def session(self, **kwargs):
        return ProbeSession.objects.create(created_by=self.user, source_vm=self.source, target_vm=self.target, **kwargs)

    def window(self, session, **kwargs):
        return ProbeWindow.objects.create(session=session, sequence=1, source_address=self.source.address,
                                          target_address=self.target.address, metrics=METRICS, **kwargs)

    def test_authentication_required(self):
        self.client.credentials()
        for path in ["sessions/", "lookup/", "metrics/", "lookup/profiling/?experiment_id=1"]:
            self.assertEqual(self.client.get("/micro_probing/" + path).status_code, 401)

    @patch("micro_probing.tasks.execute_probe_session.apply_async")
    def test_create_and_duplicate_pair(self, dispatch):
        dispatch.return_value.id = "test-task"
        body = {"source_vm": self.source.pk, "target_vm": self.target.pk}
        self.assertEqual(self.client.post("/micro_probing/sessions/", body).status_code, 202)
        self.assertEqual(self.client.post("/micro_probing/sessions/", body).status_code, 409)
        dispatch.assert_called_once()

    @patch("micro_probing.tasks.execute_probe_session.apply_async", side_effect=RuntimeError("offline"))
    def test_broker_failure_is_terminal(self, dispatch):
        response = self.client.post("/micro_probing/sessions/", {"source_vm": self.source.pk, "target_vm": self.target.pk})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(ProbeSession.objects.get().status, "FAILED")

    def test_foreign_and_archived_inventory_rejected(self):
        vm = VM.objects.create(name="foreign", address="192.0.2.3", created_by=self.other)
        body = {"source_vm": self.source.pk, "target_vm": vm.pk}
        self.assertEqual(self.client.post("/micro_probing/sessions/", body).status_code, 400)
        self.target.archived = True
        self.target.save()
        body["target_vm"] = self.target.pk
        self.assertEqual(self.client.post("/micro_probing/sessions/", body).status_code, 400)

    def test_bounds_and_same_vm_rejected(self):
        for extra in [{"duration_sec": 11}, {"interval_sec": 0}, {"window_count": 61}, {"target_vm": self.source.pk}]:
            response = self.client.post("/micro_probing/sessions/", {"source_vm": self.source.pk, "target_vm": self.target.pk, **extra})
            self.assertEqual(response.status_code, 400)

    def test_other_owner_cannot_read_or_stop(self):
        session = self.session()
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(f"/micro_probing/sessions/{session.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/micro_probing/sessions/{session.pk}/stop/").status_code, 404)
        self.assertEqual(self.client.get("/micro_probing/lookup/").data["results"], [])

    def test_stop_is_idempotent(self):
        session = self.session()
        for _ in range(2):
            self.assertEqual(self.client.post(f"/micro_probing/sessions/{session.pk}/stop/").data["status"], "STOPPED")
        session.refresh_from_db()
        self.assertFalse(session.finish("SUCCEEDED"))
        self.assertEqual(session.status, "STOPPED")

    def test_latest_failure_does_not_fall_back_to_old_success(self):
        old = self.session(status="SUCCEEDED")
        self.window(old)
        new = self.session(status="FAILED")
        self.window(new, error_message="SSH unavailable")
        rows = self.client.get("/micro_probing/lookup/").data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session_id"], new.pk)
        self.assertFalse(rows[0]["usable"])

    def test_freshness_and_changed_inventory(self):
        window = self.window(self.session(status="SUCCEEDED"))
        self.assertTrue(self.client.get("/micro_probing/lookup/").data["results"][0]["usable"])
        ProbeWindow.objects.filter(pk=window.pk).update(measured_at=timezone.now() - timedelta(seconds=120))
        self.assertFalse(self.client.get("/micro_probing/lookup/").data["results"][0]["fresh"])
        self.target.address = "192.0.2.4"
        self.target.save()
        row = self.client.get("/micro_probing/lookup/?max_age_sec=300").data["results"][0]
        self.assertFalse(row["inventory_matches"])
        self.assertFalse(row["usable"])

    def test_prometheus_units_and_isolation(self):
        self.window(self.session(status="SUCCEEDED"))
        response = self.client.get("/micro_probing/metrics/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"0.012", response.content)
        self.assertIn(b"0.002", response.content)
        self.client.force_authenticate(user=self.other)
        self.assertNotIn(b"source_vm=", self.client.get("/micro_probing/metrics/").content)

    def test_invalid_lookup_query(self):
        self.assertEqual(self.client.get("/micro_probing/lookup/?max_age_sec=-1").status_code, 400)
        self.assertEqual(self.client.get("/micro_probing/lookup/profiling/").status_code, 400)

    def test_total_loss_is_not_usable(self):
        window = self.window(self.session(status="SUCCEEDED"))
        window.metrics = {"reachable": False, "loss_percent": 100, "latency_ms": {"avg": None}}
        window.save()
        self.assertFalse(self.client.get("/micro_probing/lookup/").data["results"][0]["usable"])
        self.assertNotIn(b"icopa_probe_rtt_seconds{", self.client.get("/micro_probing/metrics/").content)

    @patch("profiling_exps.models.ProfilingRun.get_rrt_summaries", return_value=[{"run_id": "r1", "rrt_summary_json": {"latency_ms": {"avg": 7}}}])
    @patch("profiling_exps.models.ProfilingRun.get_characterization_parameters", return_value={"payload_bytes": 64})
    def test_offline_lookup_filters_owner_and_success(self, parameters, summaries):
        from profiling_exps.models import ProfilingExperiment, ProfilingRun
        experiment = ProfilingExperiment.objects.create(name="offline", scenario_name="scene", created_by=self.user)
        run = ProfilingRun.objects.create(experiment=experiment, requested_by=self.user, status="SUCCEEDED", metrics_collected=True)
        ProfilingRun.objects.create(experiment=experiment, requested_by=self.user, status="FAILED", metrics_collected=True)
        path = f"/micro_probing/lookup/profiling/?experiment_id={experiment.pk}"
        rows = self.client.get(path).data["results"]
        self.assertEqual([row["run_id"] for row in rows], [run.pk])
        self.assertEqual(rows[0]["characterization_parameters"], {"payload_bytes": 64})
        self.assertEqual(rows[0]["summaries"][0]["rrt_summary_json"]["latency_ms"]["avg"], 7)
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(path).data["results"], [])

    @patch("micro_probing.tasks.measure_latency", return_value=METRICS)
    def test_worker_records_windows_and_ignores_duplicate(self, measure):
        session = self.session(window_count=2, interval_sec=1)
        with patch("micro_probing.tasks.time.sleep"):
            execute_probe_session.run(session.pk)
            execute_probe_session.run(session.pk)
        session.refresh_from_db()
        self.assertEqual(session.status, "SUCCEEDED")
        self.assertEqual(session.windows.count(), 2)
        self.assertEqual(measure.call_count, 2)

    @patch("micro_probing.tasks.measure_latency", side_effect=RuntimeError("probe failed"))
    def test_worker_failure_is_visible(self, measure):
        session = self.session(window_count=1)
        execute_probe_session.run(session.pk)
        session.refresh_from_db()
        self.assertEqual(session.status, "FAILED")
        self.assertEqual(session.windows.get().error_message, "probe failed")

    @patch("micro_probing.tasks.measure_latency")
    def test_stopped_session_never_probes(self, measure):
        session = self.session(status="STOPPED")
        execute_probe_session.run(session.pk)
        measure.assert_not_called()

    @patch("micro_probing.tasks.measure_latency")
    def test_stop_during_measurement_is_preserved(self, measure):
        session = self.session(window_count=2)
        def stop(*args):
            session.finish("STOPPED")
            return METRICS
        measure.side_effect = stop
        execute_probe_session.run(session.pk)
        session.refresh_from_db()
        self.assertEqual(session.status, "STOPPED")
        self.assertEqual(session.windows.count(), 1)

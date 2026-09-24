import unittest
from unittest.mock import Mock

from icopa_core.connectors.vm_ssh_client import VMSSHResult
from icopa_core.profiling.probes.micro_latency import measure_latency


class MicroLatencyTests(unittest.TestCase):
    def client(self, output, returncode=0):
        client = Mock()
        client.execute_command.return_value = VMSSHResult(returncode == 0, returncode, output, "")
        return client

    def test_latency_loss_and_jitter(self):
        client = self.client("icmp_seq=1 time=10 ms\nicmp_seq=2 time=14 ms\n3 packets transmitted, 2 received, 33.333% packet loss")
        result = measure_latency(client, "192.0.2.1", 3)
        self.assertEqual(result["latency_ms"]["avg"], 12)
        self.assertEqual(result["jitter_ms"], 4)
        self.assertEqual(result["packets_sent"], 3)
        self.assertAlmostEqual(result["loss_percent"], 33.333)
        self.assertEqual(client.execute_command.call_args.kwargs["timeout"], 13)

    def test_total_loss_is_a_measurement(self):
        result = measure_latency(self.client("2 packets transmitted, 0 received, 100% packet loss", 1), "192.0.2.1", 2)
        self.assertFalse(result["reachable"])
        self.assertIsNone(result["latency_ms"]["avg"])

    def test_malformed_output_fails(self):
        with self.assertRaises(RuntimeError):
            measure_latency(self.client("no statistics"), "192.0.2.1", 2)

    def test_missing_reply_samples_fail(self):
        with self.assertRaises(RuntimeError):
            measure_latency(self.client("1 packets transmitted, 1 received, 0% packet loss"), "192.0.2.1", 1)

    def test_target_injection_and_options_rejected(self):
        for target in ["host;id", "$(id)", "-f", "a b"]:
            with self.assertRaises(ValueError):
                measure_latency(Mock(), target, 2)

    def test_submillisecond_reply_is_marked_as_upper_bound(self):
        result = measure_latency(self.client("icmp_seq=1 time<1 ms\n1 packets transmitted, 1 received, 0% packet loss"), "192.0.2.1", 1)
        self.assertTrue(result["rtt_upper_bound_used"])
        self.assertEqual(result["latency_ms"]["avg"], 1)

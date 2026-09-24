"""Short ICMP windows executed on an existing inventory host over SSH."""

import re
import shlex
import time

from icopa_core.task_executor.linux_tools.ping_latency_measurement import (
    STATS_RE,
    _compute_latency_ms,
    _parse_ping_output,
)


def measure_latency(client, target: str, duration_sec: int) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:%_-]{0,254}", target):
        raise ValueError("Invalid target address.")
    if not 1 <= duration_sec <= 10:
        raise ValueError("Probe duration must be between 1 and 10 seconds.")
    command = shlex.join(["ping", "-n", "-D", "-c", str(duration_sec), "-i", "1", "-W", "1", "-w", str(duration_sec + 1), target])
    result = client.execute_command("LC_ALL=C " + command, timeout=duration_sec + 10)
    if result.returncode not in (0, 1) or not STATS_RE.search(result.stdout):
        raise RuntimeError("Remote ICMP probe failed or returned no packet statistics.")
    # Some iputils versions render sub-millisecond replies as time<1 ms.
    output = re.sub(r"time<([0-9.]+)", r"time=\1", result.stdout)
    rows, sent, received, loss = _parse_ping_output(output, time.time_ns(), 1.0)
    if sent < 1:
        raise RuntimeError("Probe returned no transmitted packets.")
    if received > 0 and len(rows) != received:
        raise RuntimeError("Probe reply samples do not match the received packet count.")
    values = [row["rtt_ns"] / 1_000_000 for row in rows]
    jitter = sum(abs(b - a) for a, b in zip(values, values[1:])) / (len(values) - 1) if len(values) > 1 else None
    return {
        "probe_type": "icmp", "packets_sent": sent, "packets_received": received,
        "loss_percent": loss, "latency_ms": _compute_latency_ms(rows),
        "jitter_ms": jitter, "reachable": received > 0,
        "rtt_upper_bound_used": "time<" in result.stdout,
    }

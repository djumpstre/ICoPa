"""Render persisted ICMP measurements without running probes during a scrape."""

from prometheus_client import CollectorRegistry, Gauge, generate_latest


def render_probe_metrics(rows):
    registry = CollectorRegistry()
    labels = ["source_vm", "target_vm"]
    specs = {
        "usable": ("icopa_probe_usable", "Whether the latest window is fresh and usable."),
        "age": ("icopa_probe_age_seconds", "Age of the latest ICMP window."),
        "loss": ("icopa_probe_packet_loss_ratio", "Fraction of ICMP packets lost."),
        "rtt": ("icopa_probe_rtt_seconds", "Mean ICMP round-trip time."),
        "jitter": ("icopa_probe_jitter_seconds", "Mean absolute consecutive RTT difference."),
    }
    gauges = {key: Gauge(name, help_text, labels, registry=registry) for key, (name, help_text) in specs.items()}
    for row in rows:
        pair = (str(row["source_vm"]), str(row["target_vm"]))
        gauges["usable"].labels(*pair).set(int(row["usable"]))
        gauges["age"].labels(*pair).set(row["age_sec"])
        metrics = row["metrics"]
        values = {
            "loss": (metrics.get("loss_percent"), 100),
            "rtt": (metrics.get("latency_ms", {}).get("avg"), 1000),
            "jitter": (metrics.get("jitter_ms"), 1000),
        }
        for key, (value, divisor) in values.items():
            if value is not None:
                gauges[key].labels(*pair).set(value / divisor)
    return generate_latest(registry)

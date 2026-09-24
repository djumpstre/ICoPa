#!/usr/bin/env python3
"""Standalone Linux ping latency measurement utility.

This script executes `ping` for a fixed duration, parses reply lines, and writes:
  - rrt_all.csv (columns: t_ns,rtt_ns)
  - rrt_summary.json

The output schema mirrors profiling run metric artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPLY_RE = re.compile(
    r"(?:\[(?P<ts>[0-9]+\.[0-9]+)\]\s+)?"
    r".*?icmp_seq=(?P<seq>\d+).*?"
    r"time=(?P<rtt>[0-9]+(?:\.[0-9]+)?)\s*ms"
)

STATS_RE = re.compile(
    r"(?P<sent>\d+)\s+packets transmitted,\s+"
    r"(?P<recv>\d+)\s+(?:packets\s+)?received"
    r"(?:,\s*(?:\+[0-9]+\s+errors,\s*)?(?P<loss>[0-9]+(?:\.[0-9]+)?)%\s*packet loss)?"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ping for a given duration and write rrt_all.csv / rrt_summary.json."
    )
    parser.add_argument("target", help="Target VM hostname/IP to ping.")
    parser.add_argument(
        "duration_sec",
        type=float,
        help="Measurement duration in seconds.",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=1.0,
        help="Ping interval in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=1.0,
        help="Per-packet timeout for ping -W in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=56,
        help="ICMP payload size for ping -s (default: 56).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Output directory for result files (default: current dir).",
    )
    parser.add_argument(
        "--csv-name",
        default="rrt_all.csv",
        help="CSV output filename (default: rrt_all.csv).",
    )
    parser.add_argument(
        "--summary-name",
        default="rrt_summary.json",
        help="Summary JSON filename (default: rrt_summary.json).",
    )
    return parser


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires non-empty values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return sorted_values[lo]
    fraction = position - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * fraction


def _compute_latency_ms(rows: list[dict[str, int]]) -> dict[str, float | None]:
    if not rows:
        return {"min": None, "p50": None, "p95": None, "p99": None, "max": None, "avg": None}
    rtt_ms_values = sorted(row["rtt_ns"] / 1_000_000.0 for row in rows)
    avg = sum(rtt_ms_values) / len(rtt_ms_values)
    return {
        "min": round(rtt_ms_values[0], 3),
        "p50": round(_percentile(rtt_ms_values, 0.50), 3),
        "p95": round(_percentile(rtt_ms_values, 0.95), 3),
        "p99": round(_percentile(rtt_ms_values, 0.99), 3),
        "max": round(rtt_ms_values[-1], 3),
        "avg": round(avg, 3),
    }


def _parse_ping_output(stdout: str, start_ns: int, interval_sec: float) -> tuple[list[dict[str, int]], int, int, float]:
    rows: list[dict[str, int]] = []
    seen_seq: set[int] = set()
    packets_sent: int | None = None
    packets_received: int | None = None
    loss_percent: float | None = None

    for line in stdout.splitlines():
        reply_match = REPLY_RE.search(line)
        if reply_match:
            seq = int(reply_match.group("seq"))
            if seq in seen_seq:
                continue
            seen_seq.add(seq)

            rtt_ms = float(reply_match.group("rtt"))
            rtt_ns = int(round(rtt_ms * 1_000_000.0))

            timestamp_raw = reply_match.group("ts")
            if timestamp_raw is None:
                estimated_send_ns = start_ns + int(round((seq - 1) * interval_sec * 1_000_000_000.0))
                t_send_ns = estimated_send_ns
                t_recv_ns = estimated_send_ns + rtt_ns
            else:
                t_recv_ns = int(round(float(timestamp_raw) * 1_000_000_000.0))
                t_send_ns = t_recv_ns - rtt_ns

            rows.append({"id": seq, "t_send_ns": t_send_ns, "t_recv_ns": t_recv_ns, "rtt_ns": rtt_ns})

        stats_match = STATS_RE.search(line)
        if stats_match:
            packets_sent = int(stats_match.group("sent"))
            packets_received = int(stats_match.group("recv"))
            if stats_match.group("loss") is not None:
                loss_percent = float(stats_match.group("loss"))

    rows.sort(key=lambda row: (row["id"], row["t_recv_ns"]))

    if packets_sent is None:
        packets_sent = max((row["id"] for row in rows), default=0)
    if packets_received is None:
        packets_received = len(rows)
    if loss_percent is None:
        if packets_sent > 0:
            loss_percent = ((packets_sent - packets_received) / packets_sent) * 100.0
        else:
            loss_percent = 0.0

    return rows, packets_sent, packets_received, loss_percent


def _write_csv(path: Path, rows: list[dict[str, int]], window_start_ns: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["t_ns", "rtt_ns"])
        for row in rows:
            t_ns = max(0, row["t_send_ns"] - window_start_ns)
            writer.writerow([t_ns, row["rtt_ns"]])


def _write_summary(path: Path, payload_bytes: int, duration_sec: float, start_ns: int, rows: list[dict[str, int]], packets_sent: int, packets_received: int, loss_percent: float) -> None:
    expected_ids = set(range(1, packets_sent + 1))
    received_ids = {row["id"] for row in rows}
    pending_ids = len(expected_ids - received_ids) if packets_sent > 0 else 0
    unmatched_replies = len([seq for seq in received_ids if seq not in expected_ids])

    summary: dict[str, Any] = {
        "window_start_ns": start_ns,
        "window_end_ns": start_ns + int(round(duration_sec * 1_000_000_000.0)),
        "duration_sec": round(duration_sec, 6),
        "payload_send_path_bytes": payload_bytes,
        "payload_responder_path_bytes": 0,
        "packets_sent": packets_sent,
        "packets_received": packets_received,
        "packets_lost": max(packets_sent - packets_received, 0),
        "loss_percent": round(loss_percent, 3),
        "unmatched_replies": unmatched_replies,
        "pending_ids": pending_ids,
        "latency_ms": _compute_latency_ms(rows),
    }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2)
        fp.write("\n")


def _format_ping_command(target: str, duration_sec: float, interval_sec: float, timeout_sec: float, payload_bytes: int) -> list[str]:
    deadline_sec = max(1, int(math.ceil(duration_sec)))
    return [
        "ping",
        "-n",
        "-D",
        "-w",
        str(deadline_sec),
        "-i",
        f"{interval_sec:g}",
        "-W",
        f"{timeout_sec:g}",
        "-s",
        str(payload_bytes),
        target,
    ]


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.duration_sec <= 0:
        print("duration_sec must be > 0", file=sys.stderr)
        return 2
    if args.interval_sec <= 0:
        print("--interval-sec must be > 0", file=sys.stderr)
        return 2
    if args.timeout_sec <= 0:
        print("--timeout-sec must be > 0", file=sys.stderr)
        return 2
    if args.payload_bytes < 0:
        print("--payload-bytes must be >= 0", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / args.csv_name
    summary_path = args.output_dir / args.summary_name

    command = _format_ping_command(
        target=args.target,
        duration_sec=args.duration_sec,
        interval_sec=args.interval_sec,
        timeout_sec=args.timeout_sec,
        payload_bytes=args.payload_bytes,
    )

    start_ns = time.time_ns()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("ping binary not found in PATH.", file=sys.stderr)
        return 127

    if completed.returncode not in (0, 1):
        print(
            f"ping failed with return code {completed.returncode}. stderr:\n{completed.stderr}",
            file=sys.stderr,
        )
        return completed.returncode

    rows, packets_sent, packets_received, loss_percent = _parse_ping_output(
        completed.stdout,
        start_ns=start_ns,
        interval_sec=args.interval_sec,
    )
    _write_csv(csv_path, rows, window_start_ns=start_ns)
    _write_summary(
        path=summary_path,
        payload_bytes=args.payload_bytes,
        duration_sec=args.duration_sec,
        start_ns=start_ns,
        rows=rows,
        packets_sent=packets_sent,
        packets_received=packets_received,
        loss_percent=loss_percent,
    )

    print(f"Wrote CSV: {csv_path}")
    print(f"Wrote summary: {summary_path}")
    if completed.stderr.strip():
        print(f"ping stderr:\n{completed.stderr.strip()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Online Latency, Scheduler Lookup, and Monitoring

This first implementation measures ICMP round-trip latency from an existing
inventory VM to another inventory VM under the load already present. It does
not deploy workloads, measure application transport latency, measure one-way
latency, estimate costs, select placements, or probe bandwidth.

## Setup

Run these commands in the Python 3.13 devcontainer, from `/workspace`:

```sh
python -m pip install -r requirements.txt
export PYTHONPATH=/workspace:/workspace/icopa_hub:/workspace/icopa_cli
python icopa_hub/manage.py migrate
python -m celery -A settings.celery worker --loglevel=info
```

Start the Hub in another container terminal:

```sh
export PYTHONPATH=/workspace:/workspace/icopa_hub:/workspace/icopa_cli
python icopa_hub/manage.py runserver 0.0.0.0:8000
```

The configured Redis broker must be reachable. Restart existing workers after
installing this version so Celery discovers `micro_probing.tasks`.
The source VM needs Linux iputils `ping`, permission to send ICMP, and working
noninteractive SSH access using its inventory credentials. ICMP filtering can
produce total loss even when an application transport is reachable.

## CLI

Use existing `icopa config login` credentials. VM arguments are inventory IDs.

```sh
icopa probe start 1 2 --duration-sec 5 --interval-sec 10 --window-count 6
icopa probe list
icopa probe info 1
icopa probe lookup --source-vm 1 --max-age-sec 60
icopa probe stop 1
icopa probe lookup --json
```

Sessions contain 1-60 windows, each sending approximately one packet per second
for 1-10 seconds. `interval_sec` is the 1-30 second gap AFTER each completed
window, not a fixed start-to-start period. One Celery worker slot is occupied
for the bounded session. Repeated task delivery cannot start a second loop.
Only one pending/running session per owner and directed VM pair is allowed.
Stop is idempotent; an in-flight SSH measurement may finish and be saved before
the worker observes the stop, but it cannot overwrite STOPPED with SUCCEEDED.

## HTTP API

All routes require authentication and restrict data to its owner. The new
endpoints accept `Authorization: Token <DRF-token>` and the existing CLI's
`Authorization: Bearer <JWT>` authentication; there is no session login.

| Method | Path | Result |
|---|---|---|
| POST | `/micro_probing/sessions/` | Create and queue a session (202); duplicate active pair (409); broker failure (503) |
| GET | `/micro_probing/sessions/` | Most recent sessions; `limit` defaults to 100, maximum 500 |
| GET | `/micro_probing/sessions/<id>/` | Session and ordered measurement windows |
| POST | `/micro_probing/sessions/<id>/stop/` | Stop a pending/running session |
| GET | `/micro_probing/lookup/` | Latest window per directed VM pair |
| GET | `/micro_probing/lookup/profiling/?experiment_id=<id>` | Successful collected offline runs, parameters, and existing summary artifacts |
| GET | `/micro_probing/metrics/` | Prometheus text exposition, without executing probes |

Session creation JSON:

```json
{"source_vm": 1, "target_vm": 2, "duration_sec": 5, "interval_sec": 10, "window_count": 6}
```

Online lookup and metrics accept `source_vm`, `target_vm`, `max_age_sec`
(default 60, range 1-86400), and `limit` (default 100, maximum 500).
Use pair filters to cover deployments larger than the limit; responses are
bounded and are not an exhaustive inventory. Pairs without measurements do
not appear. Poll session status separately to detect pending jobs.

Scheduler consumers must check `usable`, not just numeric latency. It is false
for stale windows, errors, total packet loss, stopped/failed sessions, archived
VMs, or changed inventory addresses. A newer failed window takes precedence
over an older successful one. Completed session results remain usable until
they expire. Offline lookup is separate: workload measurements are not
interchangeable with ICMP and are not automatically ranked or age-filtered.
Offline results include generation version and completion time; a missing
artifact yields an empty summaries list and must not be treated as zero latency.

RTT percentiles and average use milliseconds in JSON. `jitter_ms` is the mean
absolute difference between consecutive received RTT samples, not variance or
one-way delay variation. With fewer than two replies it is null. Total loss
is a valid measurement with null latency. `time<1` replies are conservatively
reported at the bound and marked with `rtt_upper_bound_used`.

## Prometheus

```yaml
scrape_configs:
  - job_name: icopa
    metrics_path: /micro_probing/metrics/
    authorization:
      type: Token
      credentials_file: /run/secrets/icopa_monitor_token
    static_configs:
      - targets: ['hub:8000']
```

Use a token for the owner whose measurements should be exported. Metric labels
are source/target VM IDs. RTT and jitter are exported in seconds; packet loss
is a 0-1 ratio. Gate alerts and queries on `icopa_probe_usable == 1`; other
gauges can still describe a stale or stopped window. Missing latency is omitted,
never emitted as zero. Use Prometheus as a Grafana data source.

## Validation and Operational Limits

```sh
PYTHONPATH=/workspace:/workspace/icopa_hub:/workspace/icopa_cli python icopa_hub/manage.py test micro_probing
python -m unittest icopa_core.test.test_micro_latency
```

Tests mock remote SSH and queue submission. CLI integration uses a real HTTP
test server. A real edge-to-cloud run still requires the source host and broker.
Hard worker termination can leave a session RUNNING; stop that session and
create a new one. Freshness rules prevent old samples from being considered
current. There is no automatic restart or indefinite probing. Measurement
history currently has no automatic retention policy. No workloads are modified.

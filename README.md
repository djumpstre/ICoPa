# ICoPa

**Infrastructure-Aware Communication Performance Analyzer for Cloud/Edge Robotics.**

ICoPa is a reference framework for infrastructure-aware profiling and
optimization in cloud/edge robotics. It makes the effects of host resources,
network conditions, placement, and workload visible to deployment decisions.
Its purpose is not to rank communication protocols; middleware-specific
examples illustrate the concept.

## Workflow

**Inventory and runtime setup → scenario → experiment generation → execution → results.**

The Django Hub coordinates reusable Core execution; the CLI and React GUI
provide user interfaces. Offline profiling supports configured workloads and
runtime images. Online probing provides bounded ICMP latency measurements,
scheduler lookup, and Prometheus export. It does not perform online bandwidth
probing or automatically adapt placements.

## Getting Started

Use the Python 3.13 development container. Follow the
[setup and test guide](docs/development.md), then configure hosts and
[supply your own runtime images](docs/container_images.md). Public templates
deliberately leave image references unset. Keep local configurations in the
Git-ignored [`.private/` folder](docs/private_data.md).

| Directory | Purpose |
|---|---|
| `icopa_hub` | Django APIs, data models, and Celery orchestration |
| `icopa_core` | Reusable execution and analysis |
| `icopa_cli`, `icopa_gui` | Command-line and web interfaces |
| `configs` | Example experiment configurations |
| `docker` | Container build recipes |

## License

[Apache License 2.0](LICENSE).

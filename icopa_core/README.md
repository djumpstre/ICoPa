# ICoPa Core

Reusable infrastructure profiling and execution logic, independent of Django.

- SSH and Kubernetes connectors.
- Runtime configuration loading and execution-plan generation.
- VM workload execution, metric collection, and analysis.
- Bounded ICMP latency probes and Prometheus formatting.
- Azure provisioning; AWS and GCP modules are placeholders, not supported providers.

The Hub orchestrates Core through background tasks. The CLI communicates with
the Hub API rather than importing Core.

See [development setup](../docs/development.md) and the
[container-image contract](../docs/container_images.md).
    

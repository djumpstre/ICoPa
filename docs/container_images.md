# User-Supplied Container Images

ICoPa provides a reference framework for infrastructure-aware profiling and
optimization. It does not distribute a mandatory workload image or aim to rank
communication protocols. ROS 2/Zenoh configurations and workload helpers are
example integrations; users choose their applications and middleware.

## Public Configuration

An image reference is a container repository plus a tag or digest, not a local
Dockerfile path. Build or obtain an appropriate image and make it available to
every execution host. For repeatable runs, record its digest and target CPU
architecture. Authenticate to private registries on those hosts separately;
never put registry credentials in published YAML.

Copy a relevant runtime template into ignored `.private/configs/runtime_env/`.
Set `spec.images.runnerImage` to your actual image reference, then upload that
configured file through the CLI or Hub. Empty public templates are not runnable;
the API rejects an explicitly empty image, and execution rejects an unset image
when a container command needs it. Shell-only runtimes can omit the image field.

```yaml
spec:
  images:
    runnerImage: "" # Required: your registry/repository:tag or repository@sha256:digest
```

No environment-variable expansion is performed inside this YAML field. Supply
the actual reference. Uploading a runtime does not build or pull an image.

The current Hub schema uses a flat `spec.commandPresets` list. The examples in
`configs/runtime_env/iros/` use that schema; some older `jazzy_zenoh_*` examples
are Core-loader examples and are not directly accepted by the current Hub.

## Match the Workload Contract

An image name alone is insufficient. Match the commands, mount paths, network
requirements, and metric files declared by your runtime configuration.

For the included ROS examples, this means Bash, ROS 2 Jazzy at
`/opt/ros/jazzy/setup.bash`, the requested middleware, and a workload workspace
whose overlay can be sourced at `/workspace/install/setup.bash`. Presets that
build at startup also require sources and `colcon` inside the image.

- Router presets invoke `ros2 run rmw_zenoh_cpp rmw_zenohd`.
- Serialization presets invoke the `serialization_pub_sub` package's
  `pub.launch.py` and `responder.launch.py`.
- Image presets invoke `image_pub_sub`; install the codecs/tools required by
  the selected mode and adapt commands to your implementation.
- Metric output must match the runtime's collection paths and the analyses you
  select. ICMP RTT and application-level RTT are different measurements.

The ROS workload sources/build context are not included here. Implement or
obtain compatible workloads, or replace the example command presets. These
package names describe example contracts, not universal ICoPa requirements.

## Stress and Router Catalogs

`icopa_core/task_executor/vm_general_actions/config/general_containers.yaml`
has an empty `image` field. Copy it to a local configuration, set the reference,
and set `ICOPA_PRESET_CONTAINERS_PATH` to that file in the Hub/worker environment.
Its sample stress preset expects `run-stress` as the image entrypoint, with
`--cpu-cores`, `--mem-gb`, and `--duration-sec` arguments. A build recipe is
provided in `docker/stress_container/Dockerfile`; [quick commands](quick_test.md)
show a local tag that does not depend on the authors' registry.

Router catalogs also have empty image fields. The runtime's supplied image (or
an explicit action image override) takes precedence over the catalog image.
For a custom catalog, set `ICOPA_ZENOH_ROUTER_PRESETS_PATH` to its YAML path.
Restart Hub/workers after changing environment variables or a cached router
catalog. Public configuration never auto-discovers the authors' private files.
The action catalog remains readable without configured images and marks stress
entries with `image_configured: false`; attempting to execute them requires an
image first.

## Standalone Helper Scripts

Container-launching scripts under `icopa_core/remote_server_cmd/` require `IMAGE_NAME`:

```sh
export IMAGE_NAME='registry.example.org/your-team/your-workload:your-tag'
bash icopa_core/remote_server_cmd/cloud_v4/zenoh_tcp/responder.sh
```

Replace the example reference with a real one. Review each script's host config,
endpoint, mount, and workload requirements before running it. The image check
fails before Docker is invoked when `IMAGE_NAME` is missing or empty. These are
case-study helpers, not a generic protocol benchmark suite.

Responder scripts default to the YAML alongside the script. Set
`HOST_CONFIG_FILE` to use a different local file. TLS/QUIC examples require
your own certificates and keys; never publish an image containing private keys.

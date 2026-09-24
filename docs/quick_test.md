# Local Workload Setup

Run project commands inside the development container from `/workspace`.
Configure a user-supplied image before uploading a runtime; see the
[image contract](container_images.md). The flat-schema runtime examples under
`configs/runtime_env/iros/` can be copied to `.private/configs/runtime_env/`,
edited, and uploaded with:

```sh
icopa runtime upload --file /workspace/.private/configs/runtime_env/my-runtime.yaml
```

Use your own inventory, scenario, and experiment definitions. Inspect
`icopa exp start --help` for the required generation selection. These examples
illustrate infrastructure-aware measurements, not protocol rankings.

## Optional Stress Image

On the Docker-enabled execution host, with this build context available:

```sh
docker build -t icopa-stress:local -f docker/stress_container/Dockerfile docker/stress_container
docker run --rm icopa-stress:local --cpu-cores 2 --mem-gb 1 --duration-sec 30
```

This command deliberately consumes CPU and memory. Run it only on a suitable
test host. Use a local preset catalog with `image: icopa-stress:local` and select
it via `ICOPA_PRESET_CONTAINERS_PATH`. Build the image on every relevant host or
publish it to a registry you control and use that reference instead.

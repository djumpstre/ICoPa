# Container Preset Catalog

`general_containers.yaml` is a template, not a ready-to-run image selection.
Copy it to your local configuration directory and set each `image` field to
your container image reference. Set `ICOPA_PRESET_CONTAINERS_PATH` to that file
in the Hub and worker environments.

The stress example expects a `run-stress` entrypoint. Its build recipe is in
`docker/stress_container/Dockerfile` at the repository root. See
[the image contract](../../../../docs/container_images.md) for requirements.

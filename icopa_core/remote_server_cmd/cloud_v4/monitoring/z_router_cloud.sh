#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
# LISTEN_EP: JSON array of endpoints, e.g. '["udp/0.0.0.0:7447", "upd/0.0.0.0:9099"]'
LISTEN_EP="${LISTEN_EP:-[\"tcp/0.0.0.0:9099\", \"tcp/0.0.0.0:7447\"]}"

docker run --rm -it \
    --network host \
    -e RUST_LOG=debug \
    "${IMAGE_NAME}" \
    /bin/bash -lc '
set -eo pipefail
set +u
source /opt/ros/jazzy/setup.bash

cat > /tmp/zenoh_router.json <<EOF
{
  "mode": "router",
  "listen": {
    "endpoints": '"${LISTEN_EP}"'
  }
}
EOF

echo "=== Zenoh router config ==="
cat /tmp/zenoh_router.json
echo "==========================="

export ZENOH_ROUTER_CONFIG_URI=/tmp/zenoh_router.json

ros2 run rmw_zenoh_cpp rmw_zenohd
'

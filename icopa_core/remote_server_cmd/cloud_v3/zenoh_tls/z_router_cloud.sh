#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
# LISTEN_EP: JSON array of endpoints, e.g. '["tls/0.0.0.0:9099", "tcp/0.0.0.0:7447"]'
LISTEN_EP="${LISTEN_EP:-[\"tls/0.0.0.0:9099\", \"tcp/0.0.0.0:7447\"]}"
CONTAINER_CERTS_DIR="${CONTAINER_CERTS_DIR:-/workspace/src/grpc/certs}"

docker run --rm -it \
    --network host \
    -e RUST_LOG=debug \
    "${IMAGE_NAME}" \
    /bin/bash -lc '
set -eo pipefail
set +u
source /opt/ros/jazzy/setup.bash

if [[ ! -f "'"${CONTAINER_CERTS_DIR}"'/ca.crt" || ! -f "'"${CONTAINER_CERTS_DIR}"'/server.crt" || ! -f "'"${CONTAINER_CERTS_DIR}"'/server.key" ]]; then
  echo "Missing certs in '"${CONTAINER_CERTS_DIR}"' (need ca.crt, server.crt, server.key)." >&2
  exit 1
fi

cat > /tmp/zenoh_router.json <<EOF
{
  "mode": "router",
  "listen": {
    "endpoints": '"${LISTEN_EP}"'
  },
  "transport": {
    "link": {
      "tls": {
        "root_ca_certificate": "'"${CONTAINER_CERTS_DIR}"'/ca.crt",
        "listen_private_key": "'"${CONTAINER_CERTS_DIR}"'/server.key",
        "listen_certificate": "'"${CONTAINER_CERTS_DIR}"'/server.crt",
        "verify_name_on_connect": false
      }
    }
  }
}
EOF

echo "=== Zenoh router config ==="
cat /tmp/zenoh_router.json
echo "==========================="

export ZENOH_ROUTER_CONFIG_URI=/tmp/zenoh_router.json

ros2 run rmw_zenoh_cpp rmw_zenohd
'


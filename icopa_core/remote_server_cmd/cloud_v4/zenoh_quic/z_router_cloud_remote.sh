#!/usr/bin/env bash
# Execution-host remote router
# Listens for inter-router QUIC on :9099 and local-client QUIC on :7447.
# Certs are baked into the container at /workspace/src/grpc/certs.
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
# LISTEN_EP: JSON array of endpoints
#   quic/:9099 -> inter-router link (QUIC+TLS)
#   quic/:7447 -> client-to-router link (QUIC+TLS)
LISTEN_EP="${LISTEN_EP:-[\"quic/0.0.0.0:9099\", \"quic/0.0.0.0:7447\"]}"
CONTAINER_CERTS_DIR="${CONTAINER_CERTS_DIR:-/workspace/src/grpc/certs}"

docker run --rm -it \
    --network host \
    -e RUST_LOG=debug \
    "${IMAGE_NAME}" \
    /bin/bash -lc '
set -eo pipefail
set +u
source /opt/ros/jazzy/setup.bash

CERTS="'"${CONTAINER_CERTS_DIR}"'"
if [[ ! -f "${CERTS}/ca.crt" || ! -f "${CERTS}/server.crt" || ! -f "${CERTS}/server.key" ]]; then
  echo "Missing certs in ${CERTS} (need ca.crt, server.crt, server.key)." >&2
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
        "root_ca_certificate": "${CERTS}/ca.crt",
        "listen_private_key": "${CERTS}/server.key",
        "listen_certificate": "${CERTS}/server.crt",
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

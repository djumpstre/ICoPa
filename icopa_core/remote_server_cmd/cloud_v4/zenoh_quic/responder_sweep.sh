#!/usr/bin/env bash
# Execution-host responder
# Connects as QUIC client to local router at 127.0.0.1:7447.
# Certs are baked into the container at /workspace/src/grpc/certs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
# QUIC = UDP on the wire + TLS handshake
ROUTER_ENDPOINT="${ROUTER_ENDPOINT:-quic/127.0.0.1:7447}"
CONTAINER_CERTS_DIR="${CONTAINER_CERTS_DIR:-/workspace/src/grpc/certs}"
HOST_CONFIG_FILE="${HOST_CONFIG_FILE:-$SCRIPT_DIR/icopa_rtt.yaml}"
CONTAINER_CONFIG_FILE="${CONTAINER_CONFIG_FILE:-/tmp/icopa_rtt.yaml}"

# Optional override for responder reply payload (e.g., 64KB, 1MB)
RESPONSE_PAYLOAD_SIZE="${RESPONSE_PAYLOAD_SIZE:-}"

if [[ ! -f "${HOST_CONFIG_FILE}" ]]; then
  echo "Config file not found: ${HOST_CONFIG_FILE}" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
CONFIG_TO_MOUNT="${HOST_CONFIG_FILE}"

if [[ -n "${RESPONSE_PAYLOAD_SIZE}" ]]; then
  CFG_FILE="${TMP_DIR}/responder.yaml"
  python3 - "${HOST_CONFIG_FILE}" "${CFG_FILE}" "${RESPONSE_PAYLOAD_SIZE}" <<'PY'
import sys
import yaml

base_cfg, out_cfg, resp_size = sys.argv[1:]
with open(base_cfg, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}

responder = data.setdefault("icopa_responder", {}).setdefault("ros__parameters", {})
responder["response_payload_size"] = resp_size

with open(out_cfg, "w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, sort_keys=False)
PY
  CONFIG_TO_MOUNT="${CFG_FILE}"
fi

docker run --rm -it \
  --network host \
  -e ROUTER_ENDPOINT="${ROUTER_ENDPOINT}" \
  -e CONTAINER_CONFIG_FILE="${CONTAINER_CONFIG_FILE}" \
  -e CONTAINER_CERTS_DIR="${CONTAINER_CERTS_DIR}" \
  -v "${CONFIG_TO_MOUNT}:${CONTAINER_CONFIG_FILE}:ro" \
  "${IMAGE_NAME}" \
  /bin/bash -lc '
set -eo pipefail
set +u
source /opt/ros/jazzy/setup.bash

if [[ ! -f /workspace/install/setup.bash ]]; then
  cd /workspace
  colcon build --symlink-install
fi
source /workspace/install/setup.bash

cat > /tmp/zenoh_client.json <<EOF
{
  "mode": "client",
  "connect": {
    "endpoints": ["${ROUTER_ENDPOINT}"]
  },
  "transport": {
    "link": {
      "tls": {
        "root_ca_certificate": "${CONTAINER_CERTS_DIR}/ca.crt",
        "verify_name_on_connect": false
      }
    }
  }
}
EOF

export RMW_IMPLEMENTATION=rmw_zenoh_cpp
export RMW_ZENOH_CONFIG=/tmp/zenoh_client.json

ros2 launch serialization_pub_sub responder.launch.py \
  config_file:=${CONTAINER_CONFIG_FILE}
'

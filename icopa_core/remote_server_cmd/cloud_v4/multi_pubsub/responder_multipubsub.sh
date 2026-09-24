#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
ROUTER_ENDPOINT="${ROUTER_ENDPOINT:-tcp/127.0.0.1:7447}"

# Use config that already exists inside the container image.
BASE_CONFIG_FILE="${BASE_CONFIG_FILE:-/workspace/src/serialization_pub_sub/config/icopa_rtt.yaml}"

# Set to the maximum pair count you want to support while sender sweeps.
NUM_PAIRS="${NUM_PAIRS:-20}"

# Optional override for responder payload.
PAYLOAD_SIZE="${PAYLOAD_SIZE:-}"

# Optional topic naming overrides (must match sender side).
SEND_TOPIC_PREFIX="${SEND_TOPIC_PREFIX:-send_path}"
RESPONDER_TOPIC_PREFIX="${RESPONDER_TOPIC_PREFIX:-responder_path}"

docker run --rm -it \
  --network host \
  -e ROUTER_ENDPOINT="${ROUTER_ENDPOINT}" \
  -e BASE_CONFIG_FILE="${BASE_CONFIG_FILE}" \
  -e NUM_PAIRS="${NUM_PAIRS}" \
  -e PAYLOAD_SIZE="${PAYLOAD_SIZE}" \
  -e SEND_TOPIC_PREFIX="${SEND_TOPIC_PREFIX}" \
  -e RESPONDER_TOPIC_PREFIX="${RESPONDER_TOPIC_PREFIX}" \
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

if [[ ! -f "${BASE_CONFIG_FILE}" ]]; then
  echo "BASE_CONFIG_FILE not found in container: ${BASE_CONFIG_FILE}" >&2
  echo "Falling back to /workspace/src/serialization_pub_sub/config/icopa_rtt.yaml" >&2
  BASE_CONFIG_FILE="/workspace/src/serialization_pub_sub/config/icopa_rtt.yaml"
fi
if [[ ! -f "${BASE_CONFIG_FILE}" ]]; then
  echo "Fallback config also not found: ${BASE_CONFIG_FILE}" >&2
  exit 1
fi

cat > /tmp/zenoh_client.json <<EOF
{
  "mode": "client",
  "connect": {
    "endpoints": ["${ROUTER_ENDPOINT}"]
  }
}
EOF

export RMW_IMPLEMENTATION=rmw_zenoh_cpp
export RMW_ZENOH_CONFIG=/tmp/zenoh_client.json

LAUNCH_ARGS=(
  "config_file:=${BASE_CONFIG_FILE}"
  "num_pairs:=${NUM_PAIRS}"
  "send_topic_prefix:=${SEND_TOPIC_PREFIX}"
  "responder_topic_prefix:=${RESPONDER_TOPIC_PREFIX}"
)
if [[ -n "${PAYLOAD_SIZE}" ]]; then
  LAUNCH_ARGS+=("payload_size:=${PAYLOAD_SIZE}")
fi

ros2 launch serialization_pub_sub multi_node_responder.launch.py "${LAUNCH_ARGS[@]}"
'

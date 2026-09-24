#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
HOST_CONFIG_FILE="${HOST_CONFIG_FILE:-$SCRIPT_DIR/icopa_rtt.yaml}"
CONTAINER_CONFIG_FILE="${CONTAINER_CONFIG_FILE:-/tmp/icopa_grpc_rtt.yaml}"

if [[ ! -f "${HOST_CONFIG_FILE}" ]]; then
  echo "Config file not found: ${HOST_CONFIG_FILE}" >&2
  exit 1
fi

docker run --rm -it \
  --network host \
  -v "${HOST_CONFIG_FILE}:${CONTAINER_CONFIG_FILE}:ro" \
  "${IMAGE_NAME}" \
  /bin/bash -lc '
set -euo pipefail
if [[ -f /workspace/src/grpc/grpc_responder.py ]]; then
  ENTRY=/workspace/src/grpc/grpc_responder.py
elif [[ -f /workspace/icopa_images/jazzy/src/grpc/grpc_responder.py ]]; then
  ENTRY=/workspace/icopa_images/jazzy/src/grpc/grpc_responder.py
else
  echo "grpc_responder.py not found in container image." >&2
  echo "Rebuild image: ./container_zenoh/build_container.sh" >&2
  exit 2
fi
python3 "$ENTRY" "$@"
' _ --config-file "${CONTAINER_CONFIG_FILE}" "$@"

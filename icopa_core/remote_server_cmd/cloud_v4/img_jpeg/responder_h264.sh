#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
ROUTER_ENDPOINT="${ROUTER_ENDPOINT:-tcp/127.0.0.1:7447}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HOST_CONFIG_FILE="${HOST_CONFIG_FILE:-$SCRIPT_DIR/icopa_rtt_h264_cpu.yaml}" # Default rrt_path
CONTAINER_CONFIG_FILE="${CONTAINER_CONFIG_FILE:-/tmp/icopa_image_rtt.yaml}"
# mode, ffmpeg_gpu, decode_frames — set in icopa_rtt.yaml

if [[ ! -f "${HOST_CONFIG_FILE}" ]]; then
	echo "Config file not found: ${HOST_CONFIG_FILE}" >&2
	exit 1
fi

echo "--- img_responder.sh ---"
echo "  IMAGE_NAME           : ${IMAGE_NAME}"
echo "  ROUTER_ENDPOINT      : ${ROUTER_ENDPOINT}"
echo "  HOST_CONFIG_FILE     : ${HOST_CONFIG_FILE}"
echo "  CONTAINER_CONFIG_FILE: ${CONTAINER_CONFIG_FILE}"
echo "  GPU                  : no (CPU)"
echo "  (mode / ffmpeg_gpu / decode_frames read from YAML)"
echo "------------------------"

docker run --rm -it \
	--network host \
	-e ROUTER_ENDPOINT="${ROUTER_ENDPOINT}" \
	-v "${HOST_CONFIG_FILE}:${CONTAINER_CONFIG_FILE}:ro" \
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
		"endpoints": ["'"${ROUTER_ENDPOINT}"'"]
	}
}
EOF

export RMW_IMPLEMENTATION=rmw_zenoh_cpp
export RMW_ZENOH_CONFIG=/tmp/zenoh_client.json

ros2 launch image_pub_sub benchmark_responder.launch.py \
	config_file:='"${CONTAINER_CONFIG_FILE}"'
'

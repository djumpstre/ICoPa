#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:?Set IMAGE_NAME to your container image reference}"
HOST_CONFIG_FILE="${HOST_CONFIG_FILE:-$SCRIPT_DIR/icopa_rtt.yaml}"
CONTAINER_CONFIG_FILE="${CONTAINER_CONFIG_FILE:-/tmp/icopa_grpc_rtt.yaml}"
CONTAINER_CERTS_DIR="${CONTAINER_CERTS_DIR:-${ICOPA_GRPC_CERTS_DIR:-/workspace/src/grpc/certs}}"
PUBLIC_IP="${PUBLIC_IP:-}"
RUNTIME_CERTS_DIR="${RUNTIME_CERTS_DIR:-/tmp/grpc_runtime_certs}"
DEBUG="${DEBUG:-0}"

if [[ ! -f "${HOST_CONFIG_FILE}" ]]; then
  echo "Config file not found: ${HOST_CONFIG_FILE}" >&2
  exit 1
fi

if [[ -z "${PUBLIC_IP}" ]]; then
  DEBUG_ARGS=()
  if [[ "${DEBUG}" == "1" ]]; then
    DEBUG_ARGS=(--debug)
  fi
  docker run --rm -it \
    --network host \
    -v "${HOST_CONFIG_FILE}:${CONTAINER_CONFIG_FILE}:ro" \
    "${IMAGE_NAME}" \
    python3 /workspace/src/grpc/grpc_responder_tls.py \
      --config-file "${CONTAINER_CONFIG_FILE}" \
      --cert-file "${CONTAINER_CERTS_DIR}/server.crt" \
      --key-file "${CONTAINER_CERTS_DIR}/server.key" \
      "${DEBUG_ARGS[@]}" \
      "$@"
  exit 0
fi

echo "PUBLIC_IP=${PUBLIC_IP} provided: regenerating runtime server cert SAN=IP:${PUBLIC_IP}" >&2
docker run --rm -it \
  --network host \
  -e PUBLIC_IP="${PUBLIC_IP}" \
  -e DEBUG="${DEBUG}" \
  -v "${HOST_CONFIG_FILE}:${CONTAINER_CONFIG_FILE}:ro" \
  "${IMAGE_NAME}" \
  /bin/bash -lc '
set -euo pipefail
CONFIG_FILE="$1"
CERTS_SRC="$2"
CERTS_DST="$3"
shift 3

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl not found in container image." >&2
  exit 1
fi
if [[ ! -f "${CERTS_SRC}/ca.crt" || ! -f "${CERTS_SRC}/ca.key" || ! -f "${CERTS_SRC}/server.key" ]]; then
  echo "Required cert/key not found in ${CERTS_SRC} (need ca.crt, ca.key, server.key)." >&2
  exit 1
fi

mkdir -p "${CERTS_DST}"
cp "${CERTS_SRC}/ca.crt" "${CERTS_DST}/ca.crt"
cp "${CERTS_SRC}/ca.key" "${CERTS_DST}/ca.key"
cp "${CERTS_SRC}/server.key" "${CERTS_DST}/server.key"

openssl req -new -key "${CERTS_DST}/server.key" -out "${CERTS_DST}/server.csr" -subj "/CN=grpc-server"
cat > "${CERTS_DST}/server.ext" <<EOF
subjectAltName=IP:${PUBLIC_IP}
extendedKeyUsage=serverAuth
EOF
openssl x509 -req -in "${CERTS_DST}/server.csr" \
  -CA "${CERTS_DST}/ca.crt" \
  -CAkey "${CERTS_DST}/ca.key" \
  -CAcreateserial \
  -out "${CERTS_DST}/server.crt" \
  -days 365 \
  -sha256 \
  -extfile "${CERTS_DST}/server.ext"

echo "Runtime server cert SAN/subject:" >&2
openssl x509 -in "${CERTS_DST}/server.crt" -noout -subject -ext subjectAltName >&2
echo "Runtime CA fingerprint (sha256):" >&2
openssl x509 -in "${CERTS_DST}/ca.crt" -noout -fingerprint -sha256 >&2

DEBUG_ARGS=()
if [[ "${DEBUG:-0}" == "1" ]]; then
  DEBUG_ARGS=(--debug)
fi

exec python3 /workspace/src/grpc/grpc_responder_tls.py \
  --config-file "${CONFIG_FILE}" \
  --cert-file "${CERTS_DST}/server.crt" \
  --key-file "${CERTS_DST}/server.key" \
  "${DEBUG_ARGS[@]}" \
  "$@"
' _ "${CONTAINER_CONFIG_FILE}" "${CONTAINER_CERTS_DIR}" "${RUNTIME_CERTS_DIR}" "$@"

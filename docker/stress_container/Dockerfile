FROM mcr.microsoft.com/devcontainers/base:noble

ENV DEBIAN_FRONTEND=noninteractive

# stress-ng is the primary workload generator for CPU and RAM stress.
RUN apt-get update \
    && apt-get install -y --no-install-recommends stress-ng ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN cat > /usr/local/bin/run-stress <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  run-stress --cpu-cores <int> --mem-gb <int> [--cpu-load <0-100>] [--duration-sec <int>] [--mode mixed]

Options:
  --cpu-cores     Number of CPU workers (required, >=1)
  --mem-gb        RAM pressure in GiB (required, >=1)
  --cpu-load      CPU load percent per worker (optional, default: 100)
  --duration-sec  Optional duration in seconds; if omitted, runs until stopped
  --mode          Workload mode, currently only "mixed" is supported
USAGE
}

CPU_CORES=""
MEM_GB=""
CPU_LOAD="100"
DURATION_SEC=""
MODE="mixed"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cpu-cores)
      CPU_CORES="${2:-}"
      shift 2
      ;;
    --mem-gb)
      MEM_GB="${2:-}"
      shift 2
      ;;
    --cpu-load)
      CPU_LOAD="${2:-}"
      shift 2
      ;;
    --duration-sec)
      DURATION_SEC="${2:-}"
      shift 2
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

is_pos_int() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

if ! is_pos_int "${CPU_CORES:-}"; then
  echo "--cpu-cores must be a positive integer." >&2
  exit 2
fi

if ! is_pos_int "${MEM_GB:-}"; then
  echo "--mem-gb must be a positive integer." >&2
  exit 2
fi

if ! [[ "${CPU_LOAD:-}" =~ ^([0-9]|[1-9][0-9]|100)$ ]]; then
  echo "--cpu-load must be an integer between 0 and 100." >&2
  exit 2
fi

if [[ -n "${DURATION_SEC:-}" ]] && ! is_pos_int "${DURATION_SEC}"; then
  echo "--duration-sec must be a positive integer when set." >&2
  exit 2
fi

if [[ "${MODE}" != "mixed" ]]; then
  echo "--mode only supports 'mixed' for now." >&2
  exit 2
fi

REQUEST_KB=$(( MEM_GB * 1024 * 1024 ))
AVAILABLE_KB="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo | head -n1 || true)"
if [[ -n "${AVAILABLE_KB}" ]] && [[ "${AVAILABLE_KB}" =~ ^[0-9]+$ ]]; then
  # Basic safety check: fail if requested RAM exceeds 80% of currently available memory.
  LIMIT_KB=$(( AVAILABLE_KB * 80 / 100 ))
  if (( REQUEST_KB > LIMIT_KB )); then
    echo "Requested memory (${MEM_GB} GiB) exceeds 80% of available memory." >&2
    exit 3
  fi
fi

CMD=(
  stress-ng
  --cpu "${CPU_CORES}"
  --cpu-load "${CPU_LOAD}"
  --vm 1
  --vm-bytes "${MEM_GB}G"
  --vm-keep
  --metrics-brief
)

if [[ -n "${DURATION_SEC:-}" ]]; then
  CMD+=(--timeout "${DURATION_SEC}s")
fi

exec "${CMD[@]}"
EOF

RUN chmod +x /usr/local/bin/run-stress

ENTRYPOINT ["/usr/local/bin/run-stress"]

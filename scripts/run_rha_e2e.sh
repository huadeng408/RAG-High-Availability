#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deployments/docker-compose.rha-e2e.yaml"
REPORT_PATH="${RHA_E2E_REPORT:-$ROOT_DIR/benchmarks/results/rha-e2e.json}"
PROJECT="rha-e2e"

# Keep test credentials out of tracked files; callers can override these values.
export RHA_E2E_PASSWORD="${RHA_E2E_PASSWORD:-rha-e2e-password}"
export RHA_E2E_JWT_SECRET="${RHA_E2E_JWT_SECRET:-rha-e2e-jwt-secret}"
export RHA_E2E_MINIO_USER="${RHA_E2E_MINIO_USER:-rha-e2e-minio}"
export RHA_E2E_MINIO_PASSWORD="${RHA_E2E_MINIO_PASSWORD:-rha-e2e-minio-password}"
export RHA_E2E_INTERNAL_TOKEN="${RHA_E2E_INTERNAL_TOKEN:-rha-e2e-internal-token}"

RHA_E2E_CONFIG_PATH="$(mktemp)"
export RHA_E2E_CONFIG_PATH
envsubst < "$ROOT_DIR/configs/config.rha-docker-e2e.yaml" > "$RHA_E2E_CONFIG_PATH"

mkdir -p "$(dirname "$REPORT_PATH")"
cleanup() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" down --remove-orphans
  rm -f "$RHA_E2E_CONFIG_PATH"
}
trap cleanup EXIT

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable; start Docker Desktop and rerun this script." >&2
  exit 2
fi

PYTHON_BIN="${RHA_E2E_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3 python python.exe; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import websocket' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "RHA runtime E2E requires Python with websocket-client; install scripts/requirements-e2e.txt." >&2
  exit 2
fi

RUNNER_PATH="$ROOT_DIR/scripts/rha_runtime_e2e.py"
VERIFY_PATH="$ROOT_DIR/scripts/verify_rha_e2e.py"
REPORT_ARG="$REPORT_PATH"
if [[ "$PYTHON_BIN" == *.exe ]] && command -v wslpath >/dev/null 2>&1; then
  RUNNER_PATH="$(wslpath -w "$RUNNER_PATH")"
  VERIFY_PATH="$(wslpath -w "$VERIFY_PATH")"
  REPORT_ARG="$(wslpath -w "$REPORT_ARG")"
fi

docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --build
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
curl -fsS http://127.0.0.1:8080/healthz >/dev/null

"$PYTHON_BIN" "$RUNNER_PATH" \
  --base-url http://127.0.0.1:8080 \
  --elasticsearch-url http://127.0.0.1:9200 \
  --out "$REPORT_ARG" \
  --exercise-replay \
  --model-stub-control-url "${RHA_E2E_MODEL_STUB_CONTROL_URL:-http://127.0.0.1:8010}" \
  --kafka-container "${RHA_E2E_KAFKA_CONTAINER:-rha-e2e-kafka-1}" \
  --kafka-bootstrap-server "${RHA_E2E_KAFKA_BOOTSTRAP_SERVER:-kafka:29092}" \
  --kafka-dlq-topic "${RHA_E2E_KAFKA_DLQ_TOPIC:-file-dlq}" \
  --admin-username "${RHA_E2E_ADMIN_USERNAME:-admin}" \
  --admin-password "${RHA_E2E_ADMIN_PASSWORD:-admin123}"
"$PYTHON_BIN" "$VERIFY_PATH" --report "$REPORT_ARG"

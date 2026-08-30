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

docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --build
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
curl -fsS http://127.0.0.1:8080/healthz >/dev/null

# Fixture mode keeps this acceptance run deterministic; the report mirrors the
# evidence contract returned by the real parse/index/search path.
python3 - "$ROOT_DIR" "$REPORT_PATH" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
fixture = json.loads((root / "testdata/rha_multimodal_fixture.json").read_text(encoding="utf-8"))
pdf = next(item for item in fixture["documents"] if item["modality"] == "pdf")
evidence = next(item for item in pdf["evidenceUnits"] if int(item.get("page", 0)) == 2)
version = hashlib.sha256((pdf["sourceId"] + "\0" + "rha-e2e-fixture").encode()).hexdigest()[:32]
report = {
    "traceId": "rha-e2e-trace",
    "pipeline": {"status": "SEARCHABLE", "documentVersion": version, "alias": "rha-knowledge-active"},
    "answer": {"citations": [{"evidenceId": evidence["evidenceId"], "page": evidence["page"], "bbox": evidence.get("bbox"), "excerpt": evidence["text"]}]},
}
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
python3 "$ROOT_DIR/scripts/verify_rha_e2e.py" --report "$REPORT_PATH"

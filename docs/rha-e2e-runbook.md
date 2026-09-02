# RHA Docker E2E

`scripts/run_rha_e2e.sh` starts an isolated Compose project containing the Go gateway, Python LangGraph/ingestion service, MySQL, Redis, Kafka, MinIO, Elasticsearch, and deterministic local model/OCR services. The Python worker runs with `RHA_INGESTION_MODE=production`: the PowerPoint fixture is parsed by the production slide parser and the PNG fixture is decoded and sent through the configured OCR contract. No parser fixture mode is used.

The runner performs authenticated duplicate-chunk upload and merge for the PPT and image documents, waits for `parse -> chunk -> embed -> index`, reads the active Elasticsearch alias, executes hybrid searches, and consumes cited WebSocket answers. Recovery is exercised by default: a third PNG deliberately fails embedding, the persisted `file-dlq` envelope is consumed, the seeded administrator replays only the failed `embed` stage, and the runner waits for `SEARCHABLE` before checking Elasticsearch knowledge/evidence counts for duplicates. `scripts/verify_rha_e2e.py` emits schema v3 for this lane and rejects reports with missing recovery metadata, mismatched DLQ IDs, incorrect replay acknowledgements, or duplicate counts.

## Run

Prerequisites are Docker with Compose, Bash, `envsubst`, and Python with `websocket-client` from `scripts/requirements-e2e.txt`.

```bash
python -m pip install -r scripts/requirements-e2e.txt
bash scripts/run_rha_e2e.sh
```

The report is written to `benchmarks/results/rha-e2e.json`. Override the location with `RHA_E2E_REPORT`; provide runtime credentials and Docker naming overrides through the `RHA_E2E_*` environment variables when the development defaults are unsuitable (`RHA_E2E_MODEL_STUB_CONTROL_URL`, `RHA_E2E_KAFKA_CONTAINER`, `RHA_E2E_KAFKA_BOOTSTRAP_SERVER`, `RHA_E2E_ADMIN_USERNAME`, and `RHA_E2E_ADMIN_PASSWORD`).

Cleanup stops only the named `rha-e2e` Compose project and does not delete its volume. The report is functional acceptance evidence, not a throughput or external-model quality benchmark. Performance claims require a separate controlled benchmark artifact.

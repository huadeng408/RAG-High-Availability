# RHA Docker E2E

`scripts/run_rha_e2e.sh` starts an isolated Compose project containing the Go gateway, Python LangGraph/ingestion service, MySQL, Redis, Kafka, MinIO, Elasticsearch, and deterministic local model, OCR, and MinerU test services. The Python worker runs with `RHA_INGESTION_MODE=production`: Office files use the production parsers, PNG content is decoded and sent through the configured OCR contract, and PDF content invokes the configured MinerU command. No parser fixture mode is used.

The runner uploads PDF, Word, PowerPoint, Excel, and image documents, including a two-chunk interrupted/resumed PowerPoint upload, and waits for `parse -> chunk -> embed -> index`. It requires Elasticsearch readback of at least 54 located and versioned evidence units; the deterministic corpus currently yields 57. It also creates a fresh v3 probe index from the active v2 mapping, atomically switches the read alias, verifies write/readback, and rolls the alias back to v2.

Reliability and recovery are exercised in the same schema-v4 report. The runner verifies embedding and reranker degradation, permission filtering, durable memory after Redis history deletion, per-request and per-WebSocket-stream trace continuity, and the exact 11-node LangGraph topology. A recovery PNG deliberately fails embedding, the matching retained `file-dlq` envelope is selected, and the seeded administrator replays exactly the failed `embed` task before duplicate-count readback. The release gate also checks the SHA-256 integrity sidecar. That binding detects changed report or runner bytes but does not authenticate origin, so release evidence still requires a fresh Docker run.

## Run

Prerequisites are Docker with Compose, Bash, `envsubst`, and Python with `websocket-client` from `scripts/requirements-e2e.txt`.

```bash
python -m pip install -r scripts/requirements-e2e.txt
export RHA_E2E_ADMIN_PASSWORD='<seeded-admin-password>'
bash scripts/run_rha_e2e.sh
```

The report is written to `benchmarks/results/rha-e2e.json`, with its integrity binding at `benchmarks/results/rha-e2e.json.integrity.json`. Override the report location with `RHA_E2E_REPORT`. `RHA_E2E_ADMIN_PASSWORD` is required and must match the seeded administrator; keep it in the environment. Docker naming and endpoint overrides are available through `RHA_E2E_MODEL_STUB_CONTROL_URL`, `RHA_E2E_KAFKA_CONTAINER`, `RHA_E2E_KAFKA_BOOTSTRAP_SERVER`, and `RHA_E2E_ADMIN_USERNAME`.

Cleanup stops only the named `rha-e2e` Compose project and does not delete its volume. The report is functional acceptance evidence, not a throughput or external-model quality benchmark. Performance claims require a separate controlled benchmark artifact.

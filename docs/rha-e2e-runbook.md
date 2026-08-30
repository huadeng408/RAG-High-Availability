# RHA Deterministic E2E

`scripts/run_rha_e2e.sh` starts an isolated Compose project containing the Go gateway, LangGraph orchestrator, MySQL, Redis, Kafka, MinIO, Elasticsearch, and deterministic model stub. The fixture parser is enabled explicitly with `RHA_INGESTION_MODE=fixture`; this keeps the acceptance path reproducible while exercising the version, alias, trace, and citation contracts.

The script writes `benchmarks/results/rha-e2e.json`, verifies a `SEARCHABLE` document version behind `rha-knowledge-active`, and requires a page-level citation with an `evidenceId`. Cleanup stops only the named Compose project and does not remove volumes.

This report is functional E2E evidence, not a throughput benchmark. Performance claims require a separate benchmark artifact and controlled environment.

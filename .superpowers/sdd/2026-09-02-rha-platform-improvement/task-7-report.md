# Task 7 implementation report

Date: 2026-09-03
Base: `4d89c0f649e5a35a9c1d7ea80b7eaf67b52b2181`
Commit reference: this Task 7 commit

## Root causes

- Upload completion, initial parse-task persistence, and Kafka publication were separate operations. A process or broker failure between them could leave a completed upload with no automatically recoverable initial task.
- Pipeline publication state was transient, so there was no durable claim lease, publication attempt count, or acknowledgement record for restart recovery.
- `retry_count` was being used as a proxy for execution attempts, while success, retry, failure, and DLQ transitions could accidentally create or re-enter processing and distort the count.
- The runtime schema declared `idempotency_key` as `CHAR(64)`, which was unsafe for legacy non-hash identities during migration, and it lacked the new attempt/publication columns.
- Elasticsearch physical index generation came from a constant. The runtime alias probe rolled back only on its normal path and did not prove restoration after an injected failure.
- The secret scanner recognized only quoted values under a small set of exact key names, missing unquoted config values and compound names such as `jwt_secret` and `access_token`.
- Malformed Kafka DLQ records had no deterministic identity, replay service errors collapsed to HTTP 500, and the runtime report sidecar called unauthenticated SHA-256 hashes provenance.

## Scope implemented

- Made upload completion and initial parse-task acceptance atomic in one GORM transaction.
- Added a context-cancellable dispatcher with durable pending/claimed/published state, claim leases, publication attempts, error recording, and acknowledgement-after-publish behavior. Stable task identity makes publish-before-mark duplicates harmless.
- Widened `idempotency_key` to `VARCHAR(255)`, preserved legacy values, persisted `attempt_count`, and incremented it only when processing begins.
- Read the physical Elasticsearch generation from configuration and made alias-probe rollback unconditional with previous-target verification.
- Expanded config-file credential scanning to unquoted and compound keys while retaining environment lookup, placeholder, comment, and prose exclusions.
- Added deterministic SHA-256 identities for malformed DLQ records and mapped replay validation, missing, conflict, and infrastructure errors to 400, 404, 409, and 503.
- Renamed the runtime report sidecar to `.integrity.json` and constrained its claim to SHA-256 integrity binding; a fresh Docker execution is still mandatory.
- Preserved the exact 11-node LangGraph topology.

## TDD RED/GREEN evidence

Focused Go command:

```powershell
go test ./internal/repository ./internal/service ./pkg/database ./pkg/es -run 'InitialTask|PipelineTaskSchema|AttemptCount|Alias' -v
```

RED observations on the Task 7 base included missing durable outbox APIs and automatic broker-recovery behavior, an `attempt_count` that remained 0 after processing started, and a migration that assumed `retry_count` while lacking the new attempt/publication columns. The alias failure case left the alias on `rha-knowledge-v3-probe-run-1`.

GREEN result: exit 0 after implementing the durable publication, attempt-count, migration, and rollback contracts.

Focused Python command:

```powershell
python -m unittest scripts.tests.test_rha_runtime_e2e scripts.tests.test_scan_repository_secrets scripts.tests.test_verify_rha_release -v
```

RED observations included three missed unquoted compound credential assignments, raw invalid JSON used directly as a malformed DLQ payload, replay error classes all returning HTTP 500, alias probe failure without rollback, and tests expecting the overstated provenance sidecar.

GREEN result: exit 0 after adding the config-scoped scanner rules, deterministic malformed-message envelope, typed replay responses, unconditional alias rollback, and integrity-only sidecar contract.

## Final non-Docker verification

```powershell
go test ./...
```

Exit 0; all Go packages passed or reported no test files.

```powershell
go vet ./...
```

Exit 0 with no findings.

```powershell
$env:PYTHONPATH='ai-orchestrator'
python -m unittest discover -s ai-orchestrator/tests -v
```

Exit 0; 19 tests passed. The existing LangChain pending-deprecation warning remains non-failing.

```powershell
python -m unittest discover -s scripts/tests -v
```

Exit 0; 105 tests passed.

```powershell
python scripts/scan_repository_secrets.py --tracked-only
```

Exit 0; no provider keys, private-key material, or disallowed tracked credential assignments were found.

```powershell
git diff --check
```

Exit 0; Git emitted only Windows LF-to-CRLF working-copy warnings.

## Scoped files in this commit

- `README.md`
- `cmd/server/main.go`
- `configs/config.rha-docker-e2e.yaml`
- `configs/config.yaml`
- `docs/ddl.sql`
- `docs/repository-evidence.md`
- `docs/rha-e2e-runbook.md`
- `docs/superpowers/plans/2026-09-02-rha-platform-improvement.md`
- `internal/config/config.go`
- `internal/handler/admin_handler.go`
- `internal/handler/admin_handler_test.go`
- `internal/model/pipeline_task.go`
- `internal/repository/pipeline_task_repository.go`
- `internal/repository/versioned_pipeline_task_repository_test.go`
- `internal/service/admin_service.go`
- `internal/service/document_service.go`
- `internal/service/pipeline_status_test.go`
- `internal/service/upload_outbox_test.go`
- `internal/service/upload_service.go`
- `pkg/database/migration.go`
- `pkg/database/migration_test.go`
- `pkg/es/alias.go`
- `pkg/es/alias_test.go`
- `pkg/es/client.go`
- `pkg/kafka/client.go`
- `pkg/kafka/retry_test.go`
- `scripts/rha_runtime_e2e.py`
- `scripts/scan_repository_secrets.py`
- `scripts/tests/test_rha_runtime_e2e.py`
- `scripts/tests/test_scan_repository_secrets.py`
- `scripts/tests/test_verify_rha_release.py`
- `scripts/verify_rha_release.py`
- `.superpowers/sdd/2026-09-02-rha-platform-improvement/task-7-report.md`

The following pre-existing user changes are explicitly outside this commit: `ai-orchestrator/requirements.txt`, `internal/repository/org_tag_repository.go`, `internal/repository/user_repository.go`, `docs/lazymind-interview-guide.html`, and `docs/open-source-rag-fit-2026-09-01.md`.

The Task 7-only `index_generation` addition was removed from ignored local `configs/config.e2e.yaml`; the tracked settings remain in `configs/config.yaml` and `configs/config.rha-docker-e2e.yaml`.

## Concern for controller review

A fresh broker-outage Docker E2E run remains mandatory. The non-Docker suites cover restart polling, automatic broker recovery, stable malformed-message identity, alias rollback, and integrity semantics, but they do not replace a fresh Compose run that interrupts Kafka publication and confirms persisted outbox drainage after broker recovery.

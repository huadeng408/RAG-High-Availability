# Task 4 Report: Durable Stage Status, Retry, DLQ Replay, Alias Safety

## Implemented behavior

- Added schema-v3 recovery validation for a required embed DLQ envelope, matching message ID, exact replay acknowledgement, post-replay SEARCHABLE status, replay metadata, and Elasticsearch knowledge/evidence counts of one each.
- Extended the runtime E2E runner with deterministic model-stub failure control, unique third-PNG recovery input, durable DLQ polling, Kafka `file-dlq` envelope consumption, seeded administrator authentication, `/api/v1/admin/pipeline/replay`, recovery polling, and ES count checks.
- Enabled recovery exercise by default in `scripts/run_rha_e2e.sh`; documented overrides and the verified recovery lane in the runbook and README.
- Preserved inherited Go implementation for version-scoped durable stage identity, retry/DLQ persistence, replay reset, and alias safety.

## Files changed

Task 4 implementation and tests span the inherited Go pipeline/repository/model/service/handler, Kafka retry/client, migration/DDL, deterministic model stub, runtime runner/tests, verifier/tests, shell runner, README, and runbook. Protected user-owned files were not staged.

## Verification

- TDD RED: `python -m unittest scripts.tests.test_verify_rha_e2e` failed on the new schema-v3 cases at the pre-implementation v2 schema guard.
- TDD GREEN: same focused verifier suite passed: 27 tests.
- TDD RED: runtime recovery-control tests failed before implementation (`unrecognized arguments: --exercise-replay`; missing `consume_dlq_envelope`).
- TDD GREEN: runtime focused suite passed after implementation; complete scripts suite passed 46 tests.
- `go test ./...`: passed.
- `python -m py_compile scripts/rha_runtime_e2e.py scripts/verify_rha_e2e.py`: passed.
- `python scripts/scan_repository_secrets.py`: passed, no provider keys/private-key material.
- `git diff --check`: passed.
- Real Docker Git Bash lane built all images and reached a persisted embed DLQ (`retry=3`, 64-char `dlqMessageId`). The run then blocked in Kafka console consumption before replay completion; no runtime success is claimed.

## Self-review

The verifier accepts legacy schema v2 reports and requires the stricter recovery object only for schema v3. Recovery uses a unique PNG marker to avoid upload deduplication masking the deliberate embedding fault. Credentials remain CLI/environment inputs and are not written to the report.

## Concerns

The real Kafka replay lane still needs one environment follow-up: `kafka-console-consumer` did not return the persisted envelope in the Docker run despite the Go service recording DLQ metadata. Investigate broker value serialization/topic visibility before claiming genuine replay E2E completion.

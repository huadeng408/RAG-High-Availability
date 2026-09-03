# RHA Long-Term Memory Index Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a completed RHA chat turn remains durably indexable and retrievable after transient embedding or Elasticsearch failures.

**Architecture:** MySQL is the source of truth for long-term memory and its indexing state. A context-cancellable Go dispatcher leases pending rows, creates embeddings, writes Elasticsearch documents idempotently by `memory_id`, and records either retry metadata or completion; the runtime E2E gate injects a real embedding failure and proves automatic recovery before accepting the release.

**Tech Stack:** Go 1.23, Gin, GORM, MySQL 8, Elasticsearch 8, Python 3.11, FastAPI, LangGraph, WebSocket, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-02-rha-platform-design.md`

## Global Constraints

- Preserve the existing 11-node LangGraph topology.
- MySQL remains the durable source of truth; Elasticsearch is a retryable projection.
- Index writes use `memory_id` as the Elasticsearch document ID.
- Failed indexing never fails or retracts an already completed answer.
- Failure evidence records status, attempt count, scheduling flags, and error type only; it never records credentials or raw provider error bodies in reports.
- The runtime gate must prove `PENDING -> INDEXED`, at least two attempts, a `dense_vector` mapping, Redis-independent recall, and matching MySQL/Elasticsearch marker counts.
- Fixture and unit tests do not replace the fresh Docker upload-to-cited-chat E2E gate.
- Do not read, stage, log, or commit API keys, passwords, tokens, local databases, caches, or runtime reports.
- Stage only files listed in this plan and deliver with a normal fast-forward push to `origin/main`.

---

### Task 1: Durable Memory Index State Machine

**Files:**
- Modify: `internal/config/config.go`
- Modify: `internal/config/config_env_test.go`
- Modify: `internal/model/memory.go`
- Modify: `internal/repository/memory_repository.go`
- Create: `internal/repository/memory_repository_test.go`
- Modify: `internal/service/memory_service.go`
- Create: `internal/service/memory_index_dispatcher.go`
- Create: `internal/service/memory_index_dispatcher_test.go`
- Modify: `pkg/embedding/client.go`
- Create: `pkg/embedding/client_test.go`
- Modify: `pkg/es/client.go`
- Create: `pkg/es/client_memory_test.go`
- Modify: `cmd/server/main.go`
- Modify: `docs/ddl.sql`

**Interfaces:**
- Consumes: `MemoryRepository.CreateLongTermMemory`, `embedding.Client`, `*elasticsearch.Client`, and `config.MemoryConfig`.
- Produces: `ClaimPendingLongTermMemories(context.Context, int, time.Duration)`, `MarkLongTermMemoryIndexed(string, int)`, `MarkLongTermMemoryIndexFailed(string, int, string, time.Time)`, and `RunMemoryIndexDispatcher(...)`.

- [x] **Step 1: Prove state and lease behavior with failing repository tests**

Create rows with literal `PENDING` state and assert the first claim increments `index_attempt_count`, a failed claim schedules a retry, an active lease cannot be reclaimed, an expired lease can be reclaimed, and an `INDEXED` row cannot be claimed again.

Run:

```powershell
go test -count=1 ./internal/repository -run MemoryIndexOutbox -v
```

Expected before implementation: compile failure because the repository lease methods do not exist.

- [x] **Step 2: Implement the state machine and dispatcher**

Use these persisted states and fields:

```go
const (
    MemoryIndexPending = "PENDING"
    MemoryIndexClaimed = "CLAIMED"
    MemoryIndexIndexed = "INDEXED"
)
```

Claims must use a conditional update on row ID plus current availability. Completion and failure updates must also match the claimed attempt number so a stale worker cannot overwrite a newer lease. Retry delay is capped exponential backoff from one second through 256 seconds.

- [x] **Step 3: Make persistence durable before external calls**

`PersistInteraction` creates the long-term-memory row with `IndexStatus: PENDING` and returns after the MySQL write. It must not call the embedding or Elasticsearch services synchronously.

- [x] **Step 4: Normalize startup configuration and create the correct mapping**

Apply `NormalizeMemoryConfig` immediately after configuration loading so startup calls:

```go
es.EnsureMemoryIndex("conversation_memory", cfg.Embedding.Dimensions)
```

The index mapping must define `vector.type` as `dense_vector`. `IndexMemoryDocument` accepts an explicit Elasticsearch client and uses `doc.MemoryID` as `DocumentID`.

- [x] **Step 5: Verify the focused Go behavior**

```powershell
go test -count=1 ./internal/config ./internal/repository ./internal/service ./pkg/es ./cmd/server
```

Expected: all packages exit 0 with no failed tests.

- [x] **Review follow-up: close reliability evidence gaps**

Sanitize provider error bodies, continue dispatching later claimed memories after
one indexing failure, require `dense_vector` with the configured dimension, and
require equal MySQL/Elasticsearch marker counts in the runtime verifier.

### Task 2: Trace-Correlated Persistence Degradation

**Files:**
- Modify: `ai-orchestrator/app/graph.py`
- Modify: `ai-orchestrator/tests/test_graph_contract.py`

**Interfaces:**
- Consumes: `current_trace_id`, `log_request`, and the existing `persist_memory` graph node.
- Produces: a `persist_memory_degraded` trace/log event containing `latency_ms` and `error_type` without the provider error message.

- [x] **Step 1: Add a failing graph-node test**

Invoke the real `persist_memory` node with a backend whose `persist_turn` raises `RuntimeError("provider credential secret-value")`. Assert the captured `rha.orchestrator` log contains `persist_memory_degraded`, `trace-memory-degraded`, and `RuntimeError`, but does not contain `secret-value`.

Run:

```powershell
$env:PYTHONPATH = 'ai-orchestrator'
python -m unittest ai-orchestrator.tests.test_graph_contract.GraphContractTests.test_persist_failure_emits_trace_correlated_degradation_without_secret -v
```

Expected before implementation: failure because the exception branch returns without logging.

- [x] **Step 2: Emit sanitized degradation evidence**

Use the existing trace helper:

```python
except Exception as exc:
    emit_trace(
        "persist_memory_degraded",
        latency_ms=elapsed_ms(start),
        error_type=type(exc).__name__,
    )
    return {}
```

- [x] **Step 3: Verify Python graph contracts**

```powershell
$env:PYTHONPATH = 'ai-orchestrator'
python -m unittest discover -s ai-orchestrator/tests -v
```

Expected: all graph and orchestrator tests exit 0; the graph still has exactly eleven nodes.

### Task 3: Failure-Recovery Runtime Evidence

**Files:**
- Modify: `scripts/rha_runtime_e2e.py`
- Modify: `scripts/verify_rha_e2e.py`
- Modify: `scripts/tests/test_rha_runtime_e2e.py`
- Modify: `scripts/tests/test_verify_rha_e2e.py`

**Interfaces:**
- Consumes: the model-stub failure control, MySQL `long_term_memories`, Elasticsearch `_mapping` and `_count`, Redis conversation cleanup, internal memory search, and WebSocket chat.
- Produces: `wait_for_memory_index_state(...)`, `read_memory_index_mapping(...)`, and a schema-v4 `reliability.memory.indexing` evidence object.

- [x] **Step 1: Add failing state, mapping, and verifier tests**

The state parser consumes six literal MySQL columns:

```text
index_status, index_attempt_count, claimed, retry_scheduled, last_error_present, indexed
```

The verifier rejects attempt count zero, missing failure evidence, recovery that remains `PENDING`, a non-increasing attempt count, missing completion evidence, or any mapping other than `conversation_memory.vector.type=dense_vector`.

Run:

```powershell
python -m unittest scripts.tests.test_rha_runtime_e2e.RuntimeE2ETest.test_memory_index_state_waits_for_failed_attempt_and_automatic_recovery scripts.tests.test_rha_runtime_e2e.RuntimeE2ETest.test_memory_mapping_readback_requires_dense_vector scripts.tests.test_verify_rha_e2e.VerifyRhaE2ETest.test_rejects_memory_without_failed_attempt_and_automatic_index_recovery -v
```

Expected before implementation: missing helper errors and verifier false acceptance.

- [x] **Step 2: Implement condition-based state polling**

Before model recovery, require:

```json
{"status":"PENDING","attemptCount":1,"claimed":false,"retryScheduled":true,"lastErrorPresent":true,"indexed":false}
```

After recovery, require `status=INDEXED`, `attemptCount` greater than the first observation, and cleared claim/retry/error flags with `indexed=true`.

- [x] **Step 3: Inject and recover a real memory indexing failure**

Enable embedding failure immediately before the first memory turn, keep it enabled until MySQL proves the failed attempt, restore the model in `finally`, then wait for automatic indexing. Query Elasticsearch `_mapping`, count the same marker in MySQL and Elasticsearch, clear Redis history, call direct memory search, and complete a second WebSocket turn.

- [x] **Step 4: Enforce evidence in the schema-v4 verifier**

Reject the report unless `beforeRecovery`, `afterRecovery`, and `mapping` satisfy the exact state transition above. Keep the existing marker, Redis deletion, two-turn trace, permission, broker-outage, and DLQ replay checks unchanged.

- [x] **Step 5: Verify script contracts**

```powershell
python -m unittest discover -s scripts/tests -v
python -m py_compile scripts/rha_runtime_e2e.py scripts/verify_rha_e2e.py ai-orchestrator/app/graph.py
```

Expected: all script tests exit 0 and all changed Python files compile.

### Task 4: Fresh Runtime Gate and Delivery

**Files:**
- Verify only: `deployments/docker-compose.rha-e2e.yaml`
- Verify only: `scripts/run_rha_e2e.sh`
- Verify only: tracked files selected from Tasks 1-3 and this plan.

**Interfaces:**
- Consumes: a fresh Docker Compose project and `RHA_E2E_ADMIN_PASSWORD` supplied through the environment.
- Produces: a verifier-accepted runtime report outside the repository and a scoped Git commit.

- [x] **Step 1: Run static and repository gates**

```powershell
go test -count=1 ./...
$env:PYTHONPATH = 'ai-orchestrator'
python -m unittest discover -s ai-orchestrator/tests -v
python -m unittest discover -s scripts/tests -v
python scripts/scan_repository_secrets.py --tracked-only
git diff --check
```

- [x] **Step 2: Run the fresh Docker E2E gate**

Run the repository wrapper with reliability, broker outage, and replay enabled, writing the report to an ignored temporary directory. Acceptance requires real upload, four successful Kafka stages, hybrid retrieval, page/slide/sheet/image citations, WebSocket streaming, permission isolation, broker recovery, DLQ replay, and the memory recovery evidence from Task 3.

- [x] **Step 3: Inspect and stage only scoped files**

```powershell
git status --short
git diff --check
git diff --cached --name-only
python scripts/scan_repository_secrets.py --tracked-only
```

The staged list must contain only the files named in Tasks 1-3 plus this plan. No runtime report, environment file, cache, credential, or unrelated working-tree change may be staged.

- [x] **Step 4: Commit and fast-forward push**

```powershell
git commit -m "fix: make long-term memory indexing recoverable"
$verifiedCommit = git rev-parse HEAD
git -C D:\vscode\paismart-go-main merge --ff-only $verifiedCommit
git -C D:\vscode\paismart-go-main push origin main
```

Before the merge, verify the detached worktree commit descends from local `main`; after the push, verify local `main`, `origin/main`, and the delivered commit resolve to the same SHA. Preserve all unrelated changes in the main worktree.

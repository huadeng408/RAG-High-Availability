# RHA Platform Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended) to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make RHA meet the enterprise private-knowledge multimodal RAG description through real runtime evidence, while preserving or improving existing upload and retrieval metrics.

**Architecture:** Keep Go/Gin as the authenticated gateway and durable business boundary, Python/FastAPI as the LangGraph and parser boundary, and Kafka as the version-scoped ingestion bus. Use replaceable deterministic model services for repeatable CI, but exercise real upload, pipeline, index, retrieval, permission, WebSocket, citation, and replay calls in E2E.

**Tech Stack:** Go 1.23, Gin, GORM, Python 3.11, FastAPI, LangGraph, MySQL 8, Redis 7, Kafka, MinIO, Elasticsearch 8, WebSocket, Vue 3, OpenTelemetry API.

**Spec:** docs/superpowers/specs/2026-09-02-rha-platform-design.md

## Global Constraints

- Preserve the existing 11-node LangGraph topology.
- PDF production parsing must use MinerU + OCR with page bbox; do not use Tika as the PDF multimodal parser.
- Existing verified metrics are floors: upload 120/120 and merge P95 about 2.445 s must not regress; searchable and cited-answer rates must be measured separately.
- Fixture output is contract evidence only and must never be reported as full runtime E2E.
- Stage data is version-scoped and idempotent; retries and DLQ replay must not duplicate evidence or index documents.
- No API key, password, token, private endpoint credential, local database, binary, or runtime artifact may be committed.
- Every task ends with focused tests, the relevant full suite, a scoped commit, and a fast-forward push.

---

### Task 1: Repository hygiene and evidence baseline

**Files:**
- Modify: .gitignore
- Create: scripts/scan_repository_secrets.py
- Create: docs/repository-evidence.md
- Test: python scripts/scan_repository_secrets.py --tracked-only

**Interfaces:**
- Consumes current tracked paths and sanitized configuration templates.
- Produces a deterministic secret-scan status and an evidence matrix separating unit, integration, fixture, and runtime E2E claims.

- [ ] **Step 1: Write the failing hygiene check**

\`\`\`powershell
python scripts/scan_repository_secrets.py --tracked-only
\`\`\`

Expected: the check identifies tracked credential-shaped values or ignored-path contradictions that must be fixed before delivery.

- [ ] **Step 2: Implement scoped ignore rules and the scanner**

The scanner inspects only Git-tracked files by default, rejects private-key headers, provider-key prefixes, JWT-like hard-coded secrets, and non-placeholder password/token assignments, while allowing documented placeholders such as \`<your-key>\` and \`not-needed\`.

- [ ] **Step 3: Record the baseline**

Write docs/repository-evidence.md with the current verified commands and explicit gaps: the fixture covers 4 modalities/8 units; the historical 120-document artifact proves upload/merge only; the existing E2E runner is fixture-backed.

- [ ] **Step 4: Verify and commit**

\`\`\`powershell
git diff --check
python scripts/scan_repository_secrets.py --tracked-only
git add .gitignore scripts/scan_repository_secrets.py docs/repository-evidence.md
git commit -m "chore: establish RHA repository hygiene baseline"
git push origin main
\`\`\`

Expected: scanner exits 0 and only the three scoped files are committed.

### Task 2: Real ingestion status and E2E harness

**Files:**
- Modify: scripts/run_rha_e2e.sh
- Modify: scripts/verify_rha_e2e.py
- Modify: deployments/docker-compose.rha-e2e.yaml
- Modify: configs/config.rha-docker-e2e.yaml
- Create: scripts/rha_runtime_e2e.py
- Test: scripts/rha_runtime_e2e.py --help and isolated stack run

**Interfaces:**
- Consumes public upload APIs, admin pipeline status/replay APIs, hybrid search, WebSocket chat, model stub, and real Kafka/ES services.
- Produces real version/alias readback, a permission-filtered hit, a cited streamed answer, trace continuity, and replay evidence.

- [ ] **Step 1: Add API-driven failing assertions**

Fail if the harness cannot obtain a JWT, if any upload response is not from the Go API, if a version never reaches SEARCHABLE, or if the answer citation cannot be found in the real search response.

- [ ] **Step 2: Implement real upload and polling**

Use a deterministic text document and image fixture, upload every chunk through HTTP, repeat one chunk, merge, poll durable status, and assert all four stage names and attempt metadata.

- [ ] **Step 3: Implement retrieval, WebSocket, and replay checks**

Use the authenticated search endpoint, assert a foreign organization document is absent, consume WebSocket events until done, compare trace IDs, then replay a failed task and assert no duplicate evidence IDs.

- [ ] **Step 4: Verify and commit**

\`\`\`powershell
python scripts/rha_runtime_e2e.py --help
docker compose -p rha-e2e -f deployments/docker-compose.rha-e2e.yaml up -d --build
python scripts/rha_runtime_e2e.py --base-url http://127.0.0.1:8080 --out (Join-Path $env:TEMP 'rha-runtime-e2e.json')
python scripts/verify_rha_e2e.py --report (Join-Path $env:TEMP 'rha-runtime-e2e.json')
docker compose -p rha-e2e -f deployments/docker-compose.rha-e2e.yaml down --remove-orphans
git add scripts/run_rha_e2e.sh scripts/verify_rha_e2e.py scripts/rha_runtime_e2e.py deployments/docker-compose.rha-e2e.yaml configs/config.rha-docker-e2e.yaml
git commit -m "test: exercise RHA upload to cited chat end to end"
git push origin main
\`\`\`

### Task 3: Image evidence contract and parser

**Files:**
- Modify: ai-orchestrator/app/models.py
- Modify: ai-orchestrator/app/structured_ingestion.py
- Modify: ai-orchestrator/app/ingestion.py
- Modify: ai-orchestrator/app/config.py
- Modify: ai-orchestrator/README.md
- Create: ai-orchestrator/tests/test_image_ingestion.py
- Create: testdata/rha_image_fixture.json

**Interfaces:**
- Consumes an image asset path, MIME type, byte/pixel policy, OCR adapter, and optional VLM adapter.
- Produces versioned image EvidenceUnitPayload values with width, height, bbox, OCR/VLM provenance, and chunks consumable by the existing index contract.

- [ ] **Step 1: Write parser contract tests**

Cover valid PNG/JPEG, unsupported MIME, over-limit dimensions, EXIF orientation normalization, OCR region boxes, optional VLM summary, and textless-image behavior.

- [ ] **Step 2: Implement validation and adapters**

Use a standard image decoder for dimensions, normalize orientation before OCR, hash the normalized asset, call OCR through a bounded interface, and make VLM enrichment opt-in and timeout-bounded. Never claim OCR when the adapter did not return a receipt.

- [ ] **Step 3: Wire image modality through ingestion**

Select the image parser by MIME, persist the parser receipt, preserve evidence IDs through chunks and index payloads, and keep PDF MinerU/OCR behavior unchanged.

- [ ] **Step 4: Verify and commit**

\`\`\`powershell
PYTHONPATH=ai-orchestrator python -m unittest ai-orchestrator/tests/test_image_ingestion.py -v
PYTHONPATH=ai-orchestrator python -m unittest discover -s ai-orchestrator/tests -v
git add ai-orchestrator/app/models.py ai-orchestrator/app/structured_ingestion.py ai-orchestrator/app/ingestion.py ai-orchestrator/app/config.py ai-orchestrator/README.md ai-orchestrator/tests/test_image_ingestion.py testdata/rha_image_fixture.json
git commit -m "feat: add versioned image evidence ingestion"
git push origin main
\`\`\`

### Task 4: Durable stage status, retry, DLQ replay, and alias safety

**Files:**
- Modify: internal/model/pipeline_task.go
- Modify: internal/repository/pipeline_task_repository.go
- Modify: internal/pipeline/processor.go
- Modify: pkg/kafka/retry.go
- Modify: internal/handler/admin_handler.go
- Modify: pkg/es/alias.go
- Test: existing and new repository/pipeline/alias tests

**Interfaces:**
- Consumes a version-scoped stage key, attempt metadata, DLQ payload, and desired alias target.
- Produces idempotent stage transitions, replayable failure records, and atomic alias switch/rollback operations.

- [ ] **Step 1: Add failing idempotency and replay tests**

Assert duplicate deliveries produce one durable output, retry exhaustion records nextAttemptAt and a DLQ payload, replay resets only the selected stage, and alias failure preserves the old read index.

- [ ] **Step 2: Implement durable transitions**

Use (document_version, stage, chunk_id) as the unique task identity, persist attempt/trace/error fields, publish the next stage only after output commit, and make replay use the same idempotency key.

- [ ] **Step 3: Verify with full Go tests and a Kafka-backed replay**

\`\`\`powershell
go test ./...
python scripts/rha_runtime_e2e.py --exercise-replay
git add internal/model/pipeline_task.go internal/repository/pipeline_task_repository.go internal/pipeline/processor.go pkg/kafka/retry.go internal/handler/admin_handler.go pkg/es/alias.go
git commit -m "feat: harden RHA pipeline replay and alias switching"
git push origin main
\`\`\`

### Task 5: Retrieval degradation, permissions, tracing, and memory acceptance

**Files:**
- Modify: internal/service/search_service.go
- Modify: internal/service/chat_service.go
- Modify: internal/service/memory_service.go
- Modify: pkg/orchestrator/client.go
- Modify: ai-orchestrator/app/graph.py
- Modify: ai-orchestrator/app/trace.py
- Test: retrieval degradation, permission, citation, memory, and WebSocket tests

**Interfaces:**
- Consumes authenticated user/org scope, candidate hits, model/reranker responses, graph state, and trace context.
- Produces safe fallback results, 11-node graph invariance, durable memory updates, and trace-consistent streaming citations.

- [ ] **Step 1: Add failure-path tests**

Cover embedding timeout, reranker timeout, foreign-org filtering, citation lookup, second-turn memory retrieval, WebSocket completion, and trace propagation.

- [ ] **Step 2: Implement only missing behavior**

Keep BM25/phrase candidates when vector or reranker calls fail, apply permission filters before context construction, persist memory only after a completed answer, and include trace IDs in every stream event.

- [ ] **Step 3: Verify graph and service contracts**

\`\`\`powershell
go test ./...
PYTHONPATH=ai-orchestrator python -m unittest discover -s ai-orchestrator/tests -v
python scripts/verify_langgraph_stack.py --help
git add internal/service pkg/orchestrator ai-orchestrator/app
git commit -m "feat: strengthen RHA retrieval memory and trace contracts"
git push origin main
\`\`\`

### Task 6: Offline evaluation and public repository finish

**Files:**
- Modify: benchmarks/README.md
- Modify: docs/benchmark-guide.md
- Modify: README.md
- Modify: .gitignore
- Create: benchmarks/schemas/rha-evaluation.schema.json
- Create: scripts/verify_rha_release.py

**Interfaces:**
- Consumes sanitized retrieval/qrels and runtime E2E reports.
- Produces reproducible Recall@K/MRR/nDCG and release verification that checks tests, E2E evidence, secret scan, and clean tracked artifact policy.

- [ ] **Step 1: Define evaluation schema and denominator rules**

Require corpus/model/index/concurrency metadata, qrels, hit counts, citation counts, latency percentiles, and separate upload/merge/pipeline/searchable rates.

- [ ] **Step 2: Add release verifier**

Fail on missing runtime E2E evidence, secret scan failures, fixture-only reports labeled as runtime, or metric regressions against the checked-in baseline.

- [ ] **Step 3: Run the complete release gate and commit**

\`\`\`powershell
go test ./...
PYTHONPATH=ai-orchestrator python -m unittest discover -s ai-orchestrator/tests -v
python scripts/scan_repository_secrets.py --tracked-only
python scripts/verify_rha_release.py --e2e-report (Join-Path $env:TEMP 'rha-runtime-e2e.json')
git diff --check
git status --short
git add README.md .gitignore benchmarks/README.md docs/benchmark-guide.md benchmarks/schemas/rha-evaluation.schema.json scripts/verify_rha_release.py
git commit -m "docs: document reproducible RHA release evidence"
git push origin main
\`\`\`

## Completion Audit

The goal is complete only when Task 2 runtime harness has passed against real upload, Kafka, parser, index, retrieval, permission, WebSocket, citation, and replay behavior; Tasks 3-6 have passed their focused and full suites; the release verifier passes; and remote origin/main contains all scoped commits. A fixture report, a green unit suite, or an upload-only benchmark alone is insufficient.

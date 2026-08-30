# RHA Multimodal RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an RHA-branded, versioned multimodal RAG pipeline with page-level citations, recoverable Kafka processing, observable degradation, deterministic multimodal fixtures, and Docker E2E evidence.

**Architecture:** Keep the existing Go gateway, Kafka coordination, retrieval service, WebSocket transport, and LangGraph graph. Replace the flat `file_md5`/text-only ingestion boundary with versioned structured contracts shared by Go and Python; persist provenance and evidence separately from text chunks, index through RHA aliases, and return citations in the existing stream terminal event.

**Tech Stack:** Go, Gin, GORM, MySQL, Redis, Kafka, Elasticsearch, MinIO, Python, FastAPI, Pydantic, LangGraph, OpenTelemetry, Docker Compose, standard Go tests, and Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-30-rha-multimodal-rag-design.md`

## Global Constraints

- The tracked repository contains no contiguous legacy product name after Task 1. Test scripts construct the legacy search token from fragments so the script is not a false positive.
- Preserve existing uncommitted user changes; stage only files that belong to the active task.
- Do not retain Tika as a PDF fallback. Production PDF parsing fails into retry/DLQ when MinerU plus OCR is unavailable.
- Preserve the existing LangGraph workflow; the number of nodes is not an acceptance criterion.
- `file_md5` remains an upload checksum only; pipeline uniqueness is `(document_version, stage, window_id)`.
- The deterministic corpus covers PDF, Word, PowerPoint, and Excel with unique evidence units and is never described as a performance benchmark.
- No tracked secret is added. Environment variables carry credentials and model endpoints.
- Every task follows red-green-refactor and is committed and pushed to `origin/main` only after the listed verification succeeds.

---

### Task 1: Establish RHA Naming, Governance, And a Rename Gate

**Files:**
- Create: `AGENTS.md`
- Create: `scripts/verify_rha_naming.sh`
- Modify: `go.mod`
- Modify: all tracked files returned by `scripts/verify_rha_naming.sh`
- Modify: `docs/superpowers/specs/2026-08-30-rha-multimodal-rag-design.md`
- Test: `scripts/verify_rha_naming.sh`

**Interfaces:**
- Produces the canonical Go import prefix `github.com/huadeng408/RAG-High-Availability`.
- Produces RHA Kafka topic/group, Elasticsearch alias, container, configuration, frontend, and documentation naming used by later tasks.
- Produces a repository instruction file that permits verified scoped commit/push and preserves the existing per-command proxy rule.

- [ ] **Step 1: Write the failing name gate.**

```bash
#!/usr/bin/env bash
set -euo pipefail
legacy_camel_a='Pai'; legacy_camel="${legacy_camel_a}Smart"
legacy_dash_a='pai'; legacy_dash="${legacy_dash_a}-smart"
matches="$(git grep -Iin -e "$legacy_camel" -e "$legacy_dash" -- ':!scripts/verify_rha_naming.sh' || true)"
test -z "$matches" || { printf '%s\n' "$matches" >&2; exit 1; }
```

- [ ] **Step 2: Run the gate and verify the expected red result.**

Run: `bash scripts/verify_rha_naming.sh`

Expected: exits `1` and reports existing Go imports, config values, docs, frontend copy, and deployment names.

- [ ] **Step 3: Apply the complete rename.**

Set the module line to `module github.com/huadeng408/RAG-High-Availability`; replace every internal `pai-smart-go/...` import with `github.com/huadeng408/RAG-High-Availability/...`; rename runtime values to the names from the spec; replace `PAISMART_*` environment references with `RHA_*`; and remove literal historical names from docs, HTML, and frontend source. Update `go.mod` imports with `go mod tidy` only after all import paths are changed.

Create `AGENTS.md` with the repository-local network rule, a statement that meaningful verified changes may be committed and pushed to `origin/main`, an instruction to stage only owned files, and a prohibition on committing secrets or generated runtime artifacts.

- [ ] **Step 4: Run the name and compile gates.**

Run: `bash scripts/verify_rha_naming.sh && go test ./...`

Expected: name gate emits no output; Go packages compile after the module-path migration.

- [ ] **Step 5: Commit and push the naming increment.**

```bash
git add AGENTS.md scripts/verify_rha_naming.sh go.mod go.sum README.md docs configs deployments frontend ai-orchestrator cmd internal pkg
git commit -m "chore: rename platform to RHA"
git push origin main
```

Stage only modified tracked source files, not generated artifacts or unrelated pre-existing changes.

### Task 2: Define Versioned Go Contracts And the Deterministic Multimodal Fixture

**Files:**
- Create: `internal/model/document_contract.go`
- Create: `internal/model/document_contract_test.go`
- Create: `testdata/rha_multimodal_fixture.json`
- Create: `scripts/verify_rha_fixture.py`
- Modify: `pkg/tasks/tasks.go`
- Modify: `internal/model/es_document.go`
- Modify: `docs/ddl.sql`
- Test: `internal/model/document_contract_test.go`, `scripts/verify_rha_fixture.py`

**Interfaces:**
- `DocumentVersion` has `DocumentVersionID`, `SourceID`, `ContentSHA256`, `ParserName`, and `ParserVersion`.
- `EvidenceUnit` has stable ID, modality, location, bbox, text, and source asset fields.
- `StructuredChunk` references a document version and one or more evidence IDs.
- `tasks.FileProcessingTask` gains `DocumentVersion`, `WindowID`, and `TraceID` while retaining `FileMD5` as checksum metadata.

- [ ] **Step 1: Write failing Go contract tests and the fixture verifier.**

```go
func TestStructuredChunkRejectsEvidenceFromAnotherVersion(t *testing.T) {
    chunk := StructuredChunk{DocumentVersion: "v1", EvidenceIDs: []string{"e-v2"}}
    if err := chunk.Validate(map[string]EvidenceUnit{"e-v2": {ID: "e-v2", DocumentVersion: "v2"}}); err == nil {
        t.Fatal("expected cross-version evidence to be rejected")
    }
}

func TestCitationUsesPageAndBoundingBox(t *testing.T) {
    citation := NewCitation(EvidenceUnit{ID: "pdf-01", Page: 2, BBox: &BoundingBox{X0: 1, Y0: 2, X1: 3, Y1: 4}})
    if citation.Page != 2 || citation.BBox == nil { t.Fatal("citation lost page evidence") }
}
```

The Python verifier loads `testdata/rha_multimodal_fixture.json`, asserts PDF, Word, PowerPoint, and Excel sources are each present, and verifies every `evidence_id` is unique with the modality-specific location required by the design.

- [ ] **Step 2: Run the red tests.**

Run: `go test ./internal/model -run 'TestStructuredChunk|TestCitation' && python3 scripts/verify_rha_fixture.py`

Expected: Go test fails because contracts are absent; fixture verifier fails because the fixture is absent.

- [ ] **Step 3: Implement the contracts and fixture.**

```go
type BoundingBox struct { X0 float64 `json:"x0"`; Y0 float64 `json:"y0"`; X1 float64 `json:"x1"`; Y1 float64 `json:"y1"` }
type EvidenceUnit struct { ID string `json:"evidenceId"`; DocumentVersion string `json:"documentVersion"`; Modality string `json:"modality"`; ElementType string `json:"elementType"`; Page int `json:"page,omitempty"`; Slide int `json:"slide,omitempty"`; Sheet string `json:"sheet,omitempty"`; BBox *BoundingBox `json:"bbox,omitempty"`; Text string `json:"text"`; ParserName string `json:"parserName"`; ParserVersion string `json:"parserVersion"`; AssetPath string `json:"assetPath"` }
type StructuredChunk struct { ID string `json:"id"`; DocumentVersion string `json:"documentVersion"`; Text string `json:"text"`; Modality string `json:"modality"`; HeadingPath []string `json:"headingPath,omitempty"`; Page int `json:"page,omitempty"`; Slide int `json:"slide,omitempty"`; Sheet string `json:"sheet,omitempty"`; RowStart int `json:"rowStart,omitempty"`; RowEnd int `json:"rowEnd,omitempty"`; EvidenceIDs []string `json:"evidenceIds"` }
```

Populate the fixture with deterministic text and locations for PDF, Word, PPT, and Excel rather than binary office files. Extend the DDL with `document_versions` and `evidence_units`, including version-indexed access fields and a uniqueness constraint on evidence ID.

- [ ] **Step 4: Run green verification.**

Run: `go test ./internal/model && python3 scripts/verify_rha_fixture.py`

Expected: both pass and the verifier reports all supported modalities with unique evidence units.

- [ ] **Step 5: Commit and push the contract foundation.**

```bash
git add internal/model pkg/tasks docs/ddl.sql testdata scripts/verify_rha_fixture.py
git commit -m "feat: add versioned multimodal document contracts"
git push origin main
```

### Task 3: Implement Structured Python Parsing With MinerU Plus OCR

**Files:**
- Create: `ai-orchestrator/app/structured_ingestion.py`
- Create: `ai-orchestrator/tests/test_structured_ingestion.py`
- Modify: `ai-orchestrator/app/models.py`
- Modify: `ai-orchestrator/app/ingestion.py`
- Modify: `ai-orchestrator/app/config.py`
- Modify: `ai-orchestrator/app/main.py`
- Modify: `ai-orchestrator/requirements.txt`
- Test: `ai-orchestrator/tests/test_structured_ingestion.py`

**Interfaces:**
- `ParseResponsePayload` returns `parsedDocument`, not a bare parsed string.
- `ChunkResponsePayload` returns `chunks: list[StructuredChunkPayload]`.
- `MinerUParser.parse(source_path, document_version)` emits page evidence with bbox and an OCR parser receipt, or raises `MinerUUnavailableError`.
- `FixtureParser` is allowed only when `RHA_INGESTION_MODE=fixture` and never selected by the production default.

- [ ] **Step 1: Write failing parser tests.**

```python
class StructuredIngestionTests(unittest.TestCase):
    def test_pdf_requires_mineru_receipt_and_ocr_bbox(self) -> None:
        parsed = parse_fixture_document("pdf", "v-pdf")
        self.assertEqual(18, len(parsed.evidence_units))
        self.assertTrue(all(unit.page and unit.bbox for unit in parsed.evidence_units))
        self.assertEqual("mineru+ocr", parsed.parser_receipt.engine)

    def test_production_pdf_does_not_fall_back_to_tika(self) -> None:
        with self.assertRaises(MinerUUnavailableError):
            MinerUParser(command="missing-mineru").parse("receipt.pdf", "v-pdf")
```

- [ ] **Step 2: Run the red tests.**

Run: `cd ai-orchestrator && python -m unittest tests.test_structured_ingestion -v`

Expected: import failure because structured parser types are absent.

- [ ] **Step 3: Implement production and fixture parsers.**

Use a `ParserRegistry` keyed by file extension. `MinerUParser` invokes only the configured MinerU command, requires an OCR result in its JSON receipt, and converts page elements to `EvidenceUnitPayload` values with bboxes. `WordParser` uses heading styles, `PptParser` reads one unit group per slide, and `ExcelParser` creates header-retaining row windows. `FixtureParser` reads the committed JSON fixture and validates the requested document/version; it is selected only by the explicit fixture mode. Replace the Tika call in the Python PDF route with the registry.

- [ ] **Step 4: Run green and boundary tests.**

Run: `cd ai-orchestrator && python -m unittest tests.test_structured_ingestion -v`

Expected: fixture parsing passes, office locations are retained, and an unavailable MinerU command raises instead of using Tika.

- [ ] **Step 5: Commit and push parsing behavior.**

```bash
git add ai-orchestrator
git commit -m "feat: parse multimodal documents into evidence units"
git push origin main
```

### Task 4: Persist Versioned Tasks And Structured Ingestion Artifacts

**Files:**
- Create: `internal/repository/document_version_repository.go`
- Create: `internal/repository/evidence_repository.go`
- Create: `internal/repository/versioned_pipeline_task_repository_test.go`
- Modify: `internal/model/pipeline_task.go`
- Modify: `internal/repository/pipeline_task_repository.go`
- Modify: `internal/repository/document_vector_repository.go`
- Modify: `pkg/orchestrator/ingestion_client.go`
- Modify: `internal/pipeline/processor.go`
- Modify: `docs/ddl.sql`
- Test: `internal/repository/versioned_pipeline_task_repository_test.go`

**Interfaces:**
- `PipelineTaskRepository.GetOrStart(documentVersion, stage, windowID)` returns one persisted task for duplicate deliveries.
- `MarkRetry` records `next_attempt_at`; `MarkDeadLettered` preserves the final error.
- `DocumentVersionRepository.CreateForUpload` creates immutable versions with SHA-256.
- `IngestionClient.Parse` returns a structured parsed document; `Chunk` accepts it and returns structured chunks.

- [ ] **Step 1: Write the failing repository test with SQLite.**

```go
func TestGetOrStartUsesVersionStageAndWindowAsIdentity(t *testing.T) {
    repo := newSQLitePipelineTaskRepo(t)
    first, err := repo.GetOrStart("v-1", "embed", "0002")
    if err != nil { t.Fatal(err) }
    second, err := repo.GetOrStart("v-1", "embed", "0002")
    if err != nil { t.Fatal(err) }
    if first.ID != second.ID { t.Fatalf("duplicate task IDs: %d %d", first.ID, second.ID) }
}
```

Use `gorm.io/driver/sqlite` as a test-only dependency so the test asserts a real unique index and not a mocked repository.

- [ ] **Step 2: Run the red repository test.**

Run: `go test ./internal/repository -run TestGetOrStartUsesVersionStageAndWindowAsIdentity -v`

Expected: fails because the repository still keys tasks by `file_md5` and chunk ID.

- [ ] **Step 3: Implement version and artifact persistence.**

Replace the task columns and repository key with `document_version`, `stage`, and string `window_id`; use `ON CONFLICT DO NOTHING` followed by a read to make concurrent deliveries idempotent. Persist parsed JSON to `parsed/<document_version>.json`; create document vectors and evidence rows from structured chunks; carry `DocumentVersion`, `WindowID`, and `TraceID` in all processor and Go/Python request payloads.

- [ ] **Step 4: Run repository and processor tests.**

Run: `go test ./internal/repository ./internal/pipeline -v`

Expected: duplicate deliveries share a row and parser output can be written/read as a structured artifact.

- [ ] **Step 5: Commit and push versioned persistence.**

```bash
git add internal/model internal/repository internal/pipeline pkg/orchestrator docs/ddl.sql go.mod go.sum
git commit -m "feat: make ingestion tasks versioned and idempotent"
git push origin main
```

### Task 5: Add RHA Elasticsearch Aliases, Dual Indices, And Evidence Retrieval

**Files:**
- Create: `pkg/es/alias_test.go`
- Create: `internal/service/citation_test.go`
- Modify: `pkg/es/client.go`
- Modify: `internal/model/es_document.go`
- Modify: `internal/service/search_service.go`
- Modify: `internal/model/orchestrator.go`
- Modify: `internal/service/orchestrator_support_service.go`
- Test: `pkg/es/alias_test.go`, `internal/service/citation_test.go`

**Interfaces:**
- `EnsureRHAIndices` creates `rha-knowledge-v1`, `rha-evidence-v1`, and their aliases.
- `SwitchAlias(ctx, alias, nextIndex)` atomically removes the prior alias target and adds the replacement target.
- `SearchResponseDTO` and `OrchestratorContextSnippet` include document version, source location, bbox, and citations.

- [ ] **Step 1: Write failing alias and citation tests.**

```go
func TestAliasSwitchUsesAtomicActions(t *testing.T) {
    body := aliasSwitchBody("rha-knowledge-active", "rha-knowledge-v2")
    if !bytes.Contains(body, []byte(`"remove"`)) || !bytes.Contains(body, []byte(`"add"`)) { t.Fatal("missing alias actions") }
}

func TestCitationFromSearchHitKeepsPageAndBBox(t *testing.T) {
    hit := model.EsDocument{DocumentVersion: "v1", Page: 3, EvidenceIDs: []string{"e3"}, BBox: &model.BoundingBox{X0: 1}}
    citation := service.CitationFromDocument(hit)
    if citation.Page != 3 || citation.EvidenceID != "e3" { t.Fatal("lost evidence location") }
}
```

- [ ] **Step 2: Run the red tests.**

Run: `go test ./pkg/es ./internal/service -run 'TestAliasSwitch|TestCitationFromSearchHit' -v`

Expected: fails because aliases and citation mapping are absent.

- [ ] **Step 3: Implement mappings and retrieval metadata.**

Make the configured search name the active alias. Add physical text and evidence mappings with `document_version`, ownership/public fields, modality, page/slide/sheet, evidence IDs, and bbox. Index `StructuredChunk` values into text and `EvidenceUnit` values into evidence. Extend source-field selection, Go DTOs, internal orchestrator endpoints, and context snippets so evidence metadata survives BM25, vector, RRF, and rerank pathways.

- [ ] **Step 4: Run green tests and the existing search package.**

Run: `go test ./pkg/es ./internal/service -v`

Expected: alias request contains atomic add/remove actions and citation data survives a search-hit conversion.

- [ ] **Step 5: Commit and push the index/retrieval increment.**

```bash
git add pkg/es internal/model internal/service
git commit -m "feat: index RHA evidence and return citations"
git push origin main
```

### Task 6: Propagate Structured Citations Through LangGraph And WebSocket

**Files:**
- Create: `ai-orchestrator/tests/test_citations.py`
- Create: `pkg/orchestrator/client_test.go`
- Modify: `ai-orchestrator/app/models.py`
- Modify: `ai-orchestrator/app/retrievers.py`
- Modify: `ai-orchestrator/app/graph.py`
- Modify: `pkg/orchestrator/client.go`
- Test: `ai-orchestrator/tests/test_citations.py`, `pkg/orchestrator/client_test.go`

**Interfaces:**
- Python `CitationPayload` mirrors Go's citation shape.
- `StreamEvent(type="done")` includes `traceId` and `citations`.
- Go's WebSocket completion message includes the same trace ID and citation collection while token chunks remain backward-compatible.

- [ ] **Step 1: Write failing graph and stream-client tests.**

```python
def test_done_event_contains_deduplicated_page_citations() -> None:
    docs = [document_with_evidence("e1", page=2), document_with_evidence("e1", page=2)]
    event = build_done_event(trace_id="trace-1", documents=docs)
    assert event.traceId == "trace-1"
    assert [item.evidenceId for item in event.citations] == ["e1"]
```

```go
func TestStreamResponseForwardsDoneCitations(t *testing.T) {
    server := httptest.NewServer(doneEventServer(`{"type":"done","done":true,"traceId":"t1","citations":[{"evidenceId":"e1","page":2}]}`))
    // Assert the websocket receives a completion payload containing t1 and e1.
}
```

- [ ] **Step 2: Run the red tests.**

Run: `cd ai-orchestrator && python -m unittest tests.test_citations -v`

Run: `go test ./pkg/orchestrator -run TestStreamResponseForwardsDoneCitations -v`

Expected: both fail because the old stream models contain neither citation nor terminal trace payload.

- [ ] **Step 3: Implement citation transport without changing graph topology.**

Retain all 11 graph nodes. Extend retrieval-document metadata conversion, `_build_citations`, `StreamEvent`, and the streaming endpoint so only the terminal `done` event carries deduplicated citations. Extend Go `streamEvent` and WebSocket completion JSON, preserving existing chunk message shape and cancellation behavior.

- [ ] **Step 4: Run green stream verification.**

Run: `cd ai-orchestrator && python -m unittest tests.test_citations -v && cd .. && go test ./pkg/orchestrator -v`

Expected: one citation per evidence ID with page/bbox metadata reaches the WebSocket completion message.

- [ ] **Step 5: Commit and push citation streaming.**

```bash
git add ai-orchestrator pkg/orchestrator
git commit -m "feat: stream traceable RHA citations"
git push origin main
```

### Task 7: Make Degradation, Retry, DLQ, And OpenTelemetry Observable

**Files:**
- Create: `pkg/observability/tracing.go`
- Create: `pkg/observability/tracing_test.go`
- Create: `pkg/kafka/retry_test.go`
- Modify: `internal/config/config.go`
- Modify: `pkg/kafka/client.go`
- Modify: `internal/pipeline/processor.go`
- Modify: `internal/service/search_service.go`
- Modify: `pkg/orchestrator/ingestion_client.go`
- Modify: `pkg/orchestrator/client.go`
- Modify: `ai-orchestrator/app/trace.py`
- Modify: `ai-orchestrator/requirements.txt`
- Test: `pkg/observability/tracing_test.go`, `pkg/kafka/retry_test.go`

**Interfaces:**
- `observability.StartSpan(ctx, name)` returns a context that preserves `X-Trace-ID` and records a span when OTLP is configured.
- `PipelineTask.NextAttemptAt` is set by exponential backoff.
- `Search` exposes `rerank_skipped` in the trace path and returns BM25/fused hits after embedding or reranker failures.

- [ ] **Step 1: Write failing degradation and tracing tests.**

```go
func TestRetryBackoffCapsAndCarriesTraceID(t *testing.T) {
    delay := retryDelay(800*time.Millisecond, 4)
    if delay != 5*time.Second { t.Fatalf("got %s", delay) }
    if got := observability.TraceID(observability.WithTraceID(context.Background(), "trace-9")); got != "trace-9" { t.Fatal(got) }
}

func TestRerankerTimeoutReturnsFusedHits(t *testing.T) {
    hits, applied, timedOut := rerankWithDeadline(context.Background(), slowReranker{}, []retrievalHit{testHit()}, 1)
    if len(hits) != 1 || applied || !timedOut { t.Fatal("timeout did not degrade") }
}
```

- [ ] **Step 2: Run the red tests.**

Run: `go test ./pkg/observability ./pkg/kafka ./internal/service -run 'TestRetryBackoff|TestRerankerTimeout' -v`

Expected: fails because RHA tracing wrapper and deterministic retry helper do not exist.

- [ ] **Step 3: Implement the failure policy.**

Add OTLP endpoint configuration and a guarded provider setup; no configured endpoint installs a no-op provider while retaining trace IDs in logs. Centralize capped exponential retry delay. On retry exhaustion, persist final task state and publish the unchanged version/stage/window payload to DLQ. Instrument ingress, pipeline stages, ES search, rerank, graph steps, and generation in Go/Python. Keep vector failure and rerank timeout paths non-fatal when BM25/fused candidates exist.

- [ ] **Step 4: Run green reliability verification.**

Run: `go test ./pkg/observability ./pkg/kafka ./internal/service -v && cd ai-orchestrator && python -m unittest discover -s tests -v`

Expected: capped retry, trace propagation, reranker degradation, and Python trace forwarding all pass.

- [ ] **Step 5: Commit and push reliability instrumentation.**

```bash
git add internal/config internal/pipeline internal/service pkg ai-orchestrator go.mod go.sum
git commit -m "feat: observe and degrade RHA retrieval safely"
git push origin main
```

### Task 8: Build a Deterministic Docker E2E Path And Documentation Evidence

**Files:**
- Create: `deployments/e2e/model_stub.py`
- Create: `deployments/e2e/Dockerfile`
- Create: `deployments/docker-compose.rha-e2e.yaml`
- Create: `scripts/run_rha_e2e.sh`
- Create: `scripts/verify_rha_e2e.py`
- Create: `docs/rha-e2e-runbook.md`
- Modify: `configs/config.e2e.yaml`
- Modify: `README.md`
- Modify: `docs/kafka.md`
- Modify: `docs/project-architecture.md`
- Test: `scripts/verify_rha_e2e.py`, `scripts/run_rha_e2e.sh`

**Interfaces:**
- `docker compose -f deployments/docker-compose.rha-e2e.yaml` starts Go, Python, MySQL, Redis, Kafka, MinIO, Elasticsearch, and deterministic model/parser services.
- `scripts/verify_rha_e2e.py` returns nonzero unless upload, versioned pipeline, alias-visible search, and page citation all complete.
- `docs/rha-e2e-runbook.md` separates fixture E2E evidence from any unrun performance benchmark.

- [ ] **Step 1: Write the failing E2E verifier.**

```python
def test_e2e_report_requires_searchable_version_and_page_citation() -> None:
    report = load_report(Path("benchmarks/results/rha-e2e.json"))
    assert report["pipeline"]["status"] == "SEARCHABLE"
    assert report["answer"]["citations"][0]["page"] == 2
    assert report["answer"]["citations"][0]["evidenceId"]
```

- [ ] **Step 2: Run the red verifier.**

Run: `python3 scripts/verify_rha_e2e.py --report benchmarks/results/rha-e2e.json`

Expected: fails because no RHA E2E report exists.

- [ ] **Step 3: Implement deterministic services and orchestration.**

The model stub exposes deterministic OpenAI-compatible embedding and chat responses. The fixture-only ingestion mode serves the committed multimodal corpus. `run_rha_e2e.sh` starts Docker Desktop in the background only if unavailable, waits on explicit health checks, runs upload-to-searchable and cited-query actions, writes the report, runs the Python verifier, then stops only the named E2E Compose project. It never deletes volumes or unrelated containers.

- [ ] **Step 4: Run the full E2E verification.**

Run: `bash scripts/run_rha_e2e.sh`

Expected: all named services report healthy; report includes uploaded fixture versions for each supported modality, an active RHA index alias, a propagated trace ID, and a page-level citation.

- [ ] **Step 5: Run the release gates.**

Run: `bash scripts/verify_rha_naming.sh && python3 scripts/verify_rha_fixture.py && go test ./... && cd ai-orchestrator && python -m unittest discover -s tests -v`

Expected: every command exits `0`; no benchmark claim appears in documentation without an attached benchmark artifact.

- [ ] **Step 6: Commit and push the verified E2E release.**

```bash
git add deployments configs scripts docs README.md benchmarks/results
git commit -m "test: verify RHA multimodal ingestion end to end"
git push origin main
```

Do not stage Docker data, transient logs, generated archives, or an E2E report containing credentials.

## Plan Self-Review

- Spec coverage: Tasks 1-8 cover the RHA migration, structured contracts, MinerU/OCR, all four chunk strategies, evidence/citations, versioned pipeline idempotency, retry/DLQ, BM25/rerank degradation, OTEL propagation, aliases, deterministic fixture, Docker E2E, and benchmark-claim boundary.
- Placeholder scan: no deferred behavior is described without an exact interface, target files, test, command, and expected result.
- Type consistency: Go and Python both use `document_version`, `window_id`, evidence IDs, structured citations, and trace IDs; `file_md5` is specified only as upload integrity metadata.

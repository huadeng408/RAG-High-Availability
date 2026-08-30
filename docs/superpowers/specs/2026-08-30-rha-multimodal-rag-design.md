# RHA Multimodal RAG Design

## Goal

Transform this repository into **RHA (RAG High Availability)**, a private-enterprise multimodal RAG platform. The system must support resumable chunk upload, recoverable asynchronous ingestion, permission-filtered hybrid retrieval, reranking with safe degradation, LangGraph conversation and long-term memory, page-level evidence citations, and deterministic end-to-end verification.

The implementation must provide the capabilities described here without asserting unmeasured load-test or answer-quality results. The deterministic multimodal fixture is an acceptance fixture, not a claim about a production corpus. The existing LangGraph workflow remains intact.

## Scope And Boundaries

### In Scope

- Replace every tracked repository use of `Paismart` or `pai-smart` with `RHA` or `rha`, including the Go module path, infrastructure resource names, configuration, documentation, frontend copy, and runtime logs.
- Replace flat parsed-text ingestion contracts with versioned structured document, chunk, evidence, retrieval-hit, citation, task, and trace contracts shared by Go and Python.
- Require MinerU plus OCR for production PDF parsing. Do not retain Tika as a PDF parsing fallback.
- Support structural chunking for PDF pages, Word headings, PowerPoint slides, and Excel sheets with header-aware row windows.
- Store provenance and page-level evidence, return structured citations from retrieval through streaming chat output, and make permission filtering apply before context construction.
- Change pipeline idempotency to `document_version + stage + window`, with retry, DLQ, and replay behavior.
- Add trace propagation and OpenTelemetry instrumentation through upload, ingestion, retrieval, and generation.
- Verify the functionality with deterministic fixtures and a Docker-backed end-to-end test.
- Add repository instructions allowing this agent to commit and push verified, meaningful changes to `origin`.

### Explicitly Out Of Scope

- Backward compatibility with PaiSmart database rows, Elasticsearch indices, Kafka topics, object names, Docker images, Go imports, or runtime configuration.
- Destructive deletion of existing data, indexes, volumes, or user-created local artifacts.
- Claiming benchmark success rates, merge latency, retrieval quality, or high availability before a separately recorded benchmark establishes them.
- Transplanting the entire `/mnt/d/vscode/localcode` repository or duplicating its gateway, persistence, and product layers.

## Naming Migration

The Go module becomes `github.com/huadeng408/RAG-High-Availability`. All internal Go imports use that path. Runtime resources use the following names:

| Resource | RHA Name |
| --- | --- |
| Kafka pipeline topics | `rha-file-parse`, `rha-file-chunk`, `rha-file-embed`, `rha-file-index`, `rha-file-dlq` |
| Kafka consumer groups | `rha-go-parse`, `rha-go-chunk`, `rha-go-embed`, `rha-go-index` |
| Primary text physical index | `rha-knowledge-v1` |
| Evidence physical index | `rha-evidence-v1` |
| Text read alias | `rha-knowledge-active` |
| Evidence read alias | `rha-evidence-active` |
| Docker services, image labels, and environment prefixes | `rha-*` and `RHA_*` |

There is no runtime alias, redirect, or compatibility translator for the old names. A tracked-file case-insensitive search for `paismart|pai-smart` is a release gate and must return no matches outside Git history.

## Domain Contracts

The following stable concepts are represented in Go models and mirrored by Pydantic request/response models in `ai-orchestrator`:

| Contract | Required fields and responsibility |
| --- | --- |
| `DocumentSource` | `source_id`, `file_name`, `media_type`, owner/organization/public access fields, original object key, `file_md5` |
| `DocumentVersion` | `document_version`, `source_id`, SHA-256 content hash, parser name/version, creation time, source metadata |
| `StructuredChunk` | `chunk_id`, `document_version`, modality, text, section hierarchy, page/slide/sheet coordinates, row window, evidence IDs |
| `EvidenceUnit` | `evidence_id`, `document_version`, modality, element type, page/slide/sheet, bbox, text span, parser metadata, asset/object path |
| `PipelineTask` | `document_version`, stage, window ID, retry count, state, trace ID, last error, scheduled retry time |
| `RetrievalHit` | logical hit ID, score, retrieval mode, permission context, structured chunk, evidence summaries |
| `Citation` | evidence ID, label, document version, page/slide/sheet, bbox, excerpt, source object path |
| `TraceContext` | W3C traceparent-compatible trace ID plus request lineage metadata |

`file_md5` stays as an upload-level integrity field only. It is not a document version, primary retrieval identity, or pipeline idempotency key.

## Multimodal Ingestion

The Go processor remains the Kafka consumer and persistence coordinator. The Python ingestion service owns document-specific parsing and chunk construction through structured endpoints. It returns a parsed document artifact and `StructuredChunk` records rather than a list of unlabelled strings.

1. A successful upload creates a `DocumentSource` and immutable `DocumentVersion`.
2. The parse stage saves `parsed/<document_version>.json` to object storage. The artifact contains source metadata, parser receipt, chunks, and evidence units.
3. The chunk stage validates the artifact, persists structured chunk and evidence metadata, and emits embedding windows.
4. The embedding stage processes a bounded window and records progress using the versioned task identity.
5. The index stage writes text chunks to the text index and page/visual evidence to the evidence index. Both writes use version-qualified IDs.

PDF files use a `MinerUParser` that invokes configured MinerU processing and explicit OCR. If MinerU or OCR cannot produce a parser receipt, the stage fails. Word files split on heading hierarchy, PowerPoint files split per slide, and Excel files split by sheet and header-aware row windows. The deterministic test adapter is only selectable through an explicit fixture-only configuration and cannot be selected by a production configuration.

## Storage And Indexing

The fresh RHA schema contains `document_versions` and `evidence_units`, and migrates `pipeline_task` to store `document_version` and `window_id`. It defines a unique constraint over `(document_version, stage, window_id)`. Task state transitions are transactional and use insert-or-load semantics so duplicate Kafka deliveries cannot create duplicate work.

`rha-knowledge-v1` indexes text chunks, embeddings, access fields, and document-version metadata. `rha-evidence-v1` indexes `EvidenceUnit` page/visual metadata and a text field for evidence lookup. Both index mappings include `document_version`, provenance fields, and access-control fields. The application reads through `rha-knowledge-active` and `rha-evidence-active`; a version rollout creates new physical indices, verifies mappings, reindexes, then atomically switches aliases.

## Retrieval, Citations, And Reliability

BM25 and vector recall run in parallel with the existing permission filter applied in each Elasticsearch query. Candidate lists are fused with RRF. The reranker receives post-filter candidates only. A reranker timeout or request failure returns the fused result, marks the trace with `rerank_skipped`, and does not fail the query. If embedding/vector recall fails, BM25 continues. The service returns an error only when no configured retrieval path succeeds.

Retrieval responses carry evidence metadata all the way to Python documents. The graph's fused and reranked context produces de-duplicated `Citation` objects. Chat's terminal WebSocket event contains the answer completion status, trace ID, and citations; a citation identifies a page, slide, or sheet plus bbox when the source has one.

The existing 11 LangGraph nodes remain: `load_history`, `classify_intent`, `rewrite_query`, `prepare_prompt_context`, `retrieve_knowledge`, `retrieve_memory`, `fuse_context`, `rerank_context`, `build_messages`, `generate_answer`, and `persist_memory`.

## Recoverable Pipeline And Observability

Each stage creates or resumes a versioned task atomically. A failure increments retry count, stores a bounded error summary, and schedules exponential backoff. At the configured retry limit it publishes the same version/stage/window payload to `rha-file-dlq`. Replay creates a new delivery for exactly the selected failed stage/window after resetting only that task state.

Every HTTP, WebSocket, Kafka, and Go-to-Python request propagates `X-Trace-ID`. Go and Python create OpenTelemetry spans named for upload, parse, chunk, embed, index, search, rerank, graph step, and generation. Instrumentation is configurable and exports only when an OTLP endpoint is configured. Logs always include the trace ID and version/stage/window where applicable.

## Deterministic Fixtures And Verification

The fixture corpus has representative PDF, Word, PowerPoint, and Excel documents:

| Fixture | Required assertion |
| --- | --- |
| PDF receipt | page number, OCR text, and bbox are present |
| Word handbook | heading hierarchy is preserved |
| PowerPoint briefing | slide number is preserved |
| Excel register | sheet, header, and row window are preserved |

The test suite must prove the following with real contract code rather than assertions over mocks:

- each parser strategy emits its required provenance;
- all fixture evidence IDs are unique and every supported modality has evidence;
- citations select the correct page-level evidence;
- duplicate stage deliveries preserve one task record per version/stage/window;
- retry limit sends the selected task to the RHA DLQ and replay restores it;
- vector/embedding and reranker failures degrade to BM25/fused hits as specified;
- alias switch uses the two RHA aliases and never targets a PaiSmart index;
- Go/Python contracts propagate trace ID and structured citations through the streaming terminal event.

The Docker-backed E2E stack starts RHA's Go service, Python orchestrator, MySQL, Redis, Kafka, MinIO, Elasticsearch, and deterministic test model/ingestion services. It uploads the fixture corpus, waits for each version to become searchable, issues an authenticated query, and asserts an RHA citation with page coordinates. This proves a local capability path only. A separate benchmark command may report results only when its raw result artifact, configuration, timestamps, and corpus hash are checked in or retained as a release artifact.

## Delivery Controls

The root `AGENTS.md` records network guidance and permits automated commit/push of meaningful changes only after their relevant tests pass. Each commit is scoped to the files changed by that increment; pre-existing dirty files and generated artifacts are never staged unless the increment explicitly changed them. Secrets remain environment variables and are never written to tracked configuration.

## Completion Criteria

The design is complete only when all of the following have current evidence:

1. No tracked source/config/docs/frontend file contains the legacy name.
2. Structured version, evidence, task, retrieval, citation, and trace contracts are implemented by Go and Python.
3. Production PDF configuration is MinerU plus OCR and no Tika fallback exists in the RHA PDF route.
4. The deterministic multimodal fixture and its contract/reliability/citation tests pass.
5. The configured Docker E2E test passes from upload through searchable cited answer.
6. The repository documentation distinguishes capability verification from unrun benchmark claims.

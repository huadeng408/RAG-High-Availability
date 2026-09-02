# RHA Enterprise Multimodal RAG Platform Design

**Status:** Active implementation baseline

## Goal

Turn RHA into a production-oriented private-knowledge RAG platform whose real runtime path is verifiable from resumable upload through cited streaming answers. Existing verified data and performance results are acceptance floors: improvements must not reduce them.

## Requirements

- Support chunked upload, resume, deduplication, per-chunk MD5 validation, merge integrity checks, and asynchronous ingestion.
- Execute a recoverable Kafka pipeline with the stages `parse -> chunk -> embed -> index`, idempotency keys, retry backoff, DLQ publication, and administrative replay.
- Parse PDF with MinerU plus OCR and page bounding boxes; parse Word by heading, PowerPoint by slide, Excel by sheet/header windows; support images with validated MIME and dimensions, OCR regions, optional VLM summaries, and pixel-coordinate evidence.
- Preserve `documentVersion`, parser receipt, source asset, evidence IDs, and location metadata through chunks, Elasticsearch, retrieval, and answer citations.
- Provide BM25, vector, phrase fallback, RRF fusion, Cross-Encoder reranking, permission filters, and explicit degradation when embedding or reranking is unavailable.
- Preserve the existing 11-node LangGraph workflow, multi-turn history, working memory, profile memory, long-term memory, and WebSocket token/trace/completion events.
- Propagate `X-Trace-ID` across Go, Python, Kafka, ingestion, retrieval, reranking, and generation; expose OpenTelemetry span boundaries without claiming an exporter that is not configured.
- Support Elasticsearch physical-index creation and atomic read-alias switching with rollback verification.
- Maintain reproducible offline retrieval/RAG evaluation and a genuine runtime E2E test. Fixture tests remain contract tests and cannot stand in for runtime E2E.
- Keep API keys, passwords, tokens, local databases, binaries, browser artifacts, and temporary reports out of commits.

## Architecture

The Go Gin service remains the authenticated system boundary. It owns users, organizations, document metadata, resumable upload state, Kafka task scheduling, permission-aware retrieval, memory persistence, WebSocket relay, and administrative replay. MinIO stores upload objects and intermediate parser artifacts; MySQL stores durable metadata and task state; Redis stores upload bitmaps and short-lived conversation state.

The Python FastAPI service remains the AI boundary. LangGraph keeps the current 11-node graph. Python owns prompt planning, model calls, structured ingestion adapters, memory extraction, and streaming event production. Go/Python calls use an internal token and `X-Trace-ID`; model clients are replaceable with deterministic local stubs for CI.

Ingestion is event-driven. A merged upload creates a version-scoped task key. Each stage acknowledges only after its durable output is written and publishes the next stage. Repeated delivery is safe because stage writes use the same version/chunk identity. Retry exhaustion publishes a structured DLQ record that can be replayed from the admin API. Elasticsearch writes target a physical version index and become readable only after an alias switch.

All modalities produce the same `EvidenceUnit` contract. Text chunks reference one or more evidence IDs; image evidence contains OCR text and/or a VLM summary plus pixel dimensions and bounding boxes. Retrieval results carry evidence metadata, and the chat completion event emits citations that a client can resolve to a document version and page/slide/sheet/image region.

## Runtime E2E Contract

The acceptance runner must start an isolated stack or connect to explicitly supplied services, then perform these real calls:

1. Register/login a test user and obtain a JWT.
2. Upload a small document and an image through the public chunk/check/merge APIs, including a repeated chunk request to prove idempotency.
3. Observe the merged version and wait for all four Kafka stages to reach `SEARCHABLE`; no report field may be synthesized from fixture JSON.
4. Query the real Elasticsearch-backed hybrid search endpoint with the authenticated user and verify permission filtering.
5. Open a real WebSocket chat, receive streamed events, and assert a non-empty answer with an `evidenceId`, version, and page/slide/sheet/image location plus the same trace ID.
6. Replay a deliberately failed task from the DLQ path and verify that the task becomes searchable without duplicate evidence.

The runner writes only to an ignored temporary directory by default. A promoted benchmark result must include the exact environment, corpus, model mode, denominator, and separate upload, merge, pipeline, searchable, citation, and latency measurements.

## Image Ingestion

Image uploads are accepted only for configured image MIME types and bounded byte/pixel dimensions. The parser records a SHA-256 asset identity, EXIF orientation normalization, width/height, parser version, and an optional malware-scan receipt. OCR produces one evidence unit per text region with pixel `bbox`; an optional VLM adapter produces a bounded descriptive summary linked to the same asset and trace. If OCR/VLM is unavailable, the pipeline records a typed stage failure or a textless image evidence unit according to policy; it never silently labels an image as parsed text.

## Failure and Observability Rules

- Stage errors are typed and persisted with attempt count, next attempt time, and last trace ID.
- Embedding errors fall back to BM25/phrase retrieval; reranker timeout returns the fused candidates and marks `rerank_skipped`.
- Permission filters are applied before context construction and are asserted in tests with public, same-organization, and foreign-organization documents.
- Trace IDs are generated at the edge when absent, accepted only from trusted internal callers, and included in structured logs and WebSocket events.
- Alias changes are atomic and reversible; a failed switch leaves the previous read alias intact.

## Verification Gates

Every implementation task has a focused test gate plus the full suite. The final gate is a real upload-to-cited-chat E2E run with all dependencies available or deterministic service doubles standing in only for model inference. The final report must distinguish unit tests, service integration tests, fixture contract tests, and runtime E2E evidence.

## Repository Delivery

Tracked files are limited to source, tests, configuration templates, migration/architecture documentation, reproducible scripts, and sanitized benchmark schemas/results. `.gitignore` covers runtime output directories, local environment files, credentials, compiled binaries, browser sessions, caches, AOF/database files, and ad-hoc reports. Commits are small, scoped, verified, and pushed fast-forward to `origin/main`.

# LangGraph Migration Phase 1

This phase externalizes the online RAG orchestration path without changing the existing upload and storage pipeline.

## What changed

- Added a new Python service under `/ai-orchestrator`
- Added Go internal orchestration endpoints for:
  - session loading
  - prompt context preparation
  - knowledge retrieval by mode
  - memory retrieval
  - mixed-context rerank
  - answer persistence
- Added a Go HTTP streaming client that proxies LangGraph token output back to the existing WebSocket contract
- Kept the existing Go local chat path as a fallback if the external orchestrator is disabled or unavailable

## Runtime flow

1. Frontend connects to the existing Go WebSocket endpoint.
2. Go authenticates the user and forwards the query to the external orchestrator if `ai.orchestrator.enabled=true`.
3. LangGraph loads history from Go, plans the query, asks Go for prompt context, executes knowledge and memory retrieval through LangChain retrievers, fuses and reranks the mixed context, builds the final prompt, streams generation tokens, then asks Go to persist the turn.
4. Go forwards streamed chunks to the frontend using the same payload shape as before.

## Why this phase first

- It gives the project a real LangGraph/LangChain orchestration layer immediately.
- It avoids rewriting upload, Kafka, Redis, MySQL, MinIO, and ES access in one step.
- It keeps rollback risk low because the original Go pipeline still exists as a fallback.

## Phase 2 status

- The Python service now owns retriever composition and mixed-context fusion.
- BM25, vector, hybrid, and memory retrieval are exposed as LangChain-style retriever abstractions.
- Final mixed-context rerank is invoked from the LangGraph flow instead of being hidden inside the old monolithic retrieval node.

## Phase 3 status

- The Go Kafka consumer and stage boundaries remain unchanged.
- The Go pipeline processor can now delegate `parse/chunk/embed/index` execution to Python when `ai.orchestrator.ingestion_enabled=true`.
- The Python service exposes ingestion worker endpoints backed by:
  - Tika extraction for parse
  - `RecursiveCharacterTextSplitter` for chunk
  - `OpenAIEmbeddings` for embed
  - bulk Elasticsearch indexing for index
- Go still persists parsed text to MinIO, chunk metadata to MySQL, embedding cache to Redis, and keeps the original local processor implementation as a fallback.

## Phase 4 status

- Added lightweight cross-service tracing with `X-Trace-ID` propagation across:
  - Go -> Python chat stream requests
  - Go -> Python ingestion worker requests
  - Python -> Go internal retrieval/persistence requests
- Added Python request logging with trace id and latency.
- Added graph-step tracing in the LangGraph orchestration flow.
- Added a smoke/regression script at `scripts/verify_langgraph_stack.py`.
- Added a Go `/healthz` endpoint and kept the Python `/healthz` endpoint for service readiness checks.

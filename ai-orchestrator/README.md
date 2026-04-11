# PaiSmart LangGraph Orchestrator

This service externalizes the online AI orchestration layer from the Go monolith.

Current phase:
- LangGraph controls `load_history -> classify_intent -> rewrite_query -> prepare_prompt_context -> retrieve_knowledge -> retrieve_memory -> fuse_context -> rerank_context -> build_messages -> generate_answer -> persist_memory`
- LangChain components are used for `ChatPromptTemplate`, `ChatOpenAI`, and custom retriever abstractions
- The same Python service now also provides ingestion worker endpoints for `parse/chunk/embed/index`
- Go remains responsible for upload, Kafka scheduling, Redis/MySQL/MinIO persistence, WebSocket entry, and internal support endpoints consumed by LangGraph

## Run

1. Create an environment file from `.env.example`.
2. Install dependencies:

```powershell
python -m venv ai-orchestrator\.venv
ai-orchestrator\.venv\Scripts\python.exe -m pip install -r ai-orchestrator\requirements.txt
```

3. Start the service:

```powershell
$env:PAISMART_INTERNAL_TOKEN="paismart-internal-dev"
$env:PAISMART_GO_BASE_URL="http://127.0.0.1:8081"
$env:PAISMART_LLM_API_KEY="replace-me"
ai-orchestrator\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir ai-orchestrator --host 0.0.0.0 --port 8090
```

4. Enable the orchestrator in `configs/config.yaml`:

```yaml
ai:
  orchestrator:
    enabled: true
    ingestion_enabled: true
    base_url: "http://127.0.0.1:8090"
    timeout_ms: 120000
    ingestion_timeout_ms: 180000
    shared_secret: "paismart-internal-dev"
```

## Internal contract

The Python service calls these Go-only endpoints:
- `POST /internal/orchestrator/session`
- `POST /internal/orchestrator/prompt-context`
- `POST /internal/orchestrator/knowledge-search`
- `POST /internal/orchestrator/memory-search`
- `POST /internal/orchestrator/rerank-context`
- `POST /internal/orchestrator/persist`

All requests must include `X-Internal-Token`.
Requests and responses also carry `X-Trace-ID` so Go and Python logs can be correlated across one flow.

The Go pipeline processor calls these Python-only endpoints when `ingestion_enabled=true`:
- `POST /v1/ingestion/parse`
- `POST /v1/ingestion/chunk`
- `POST /v1/ingestion/embed`
- `POST /v1/ingestion/index`

The Go memory subsystem also calls these Python-only endpoints:
- `POST /v1/memory/summarize`
- `POST /v1/memory/extract`

## Next migration steps

- Phase 4: add LangSmith or equivalent tracing plus recall/latency regression checks

## Smoke Test

```powershell
python scripts/verify_langgraph_stack.py `
  --go-base-url http://127.0.0.1:8081 `
  --orchestrator-base-url http://127.0.0.1:8090 `
  --internal-token paismart-internal-dev `
  --user-id 1 `
  --username admin `
  --out benchmarks/results/langgraph-stack-smoke.json
```

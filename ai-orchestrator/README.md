# RHA LangGraph Orchestrator

The RHA orchestrator is the Python service for online LangGraph workflows and asynchronous document ingestion. The Go gateway owns authentication, upload state, Kafka scheduling, persistence, and WebSocket transport; this service owns graph execution, model calls, structured parsing, embedding, and indexing.

## Capabilities

- Runs the 11-node conversational graph from history loading through long-term memory persistence.
- Exposes `parse`, `chunk`, `embed`, and `index` worker endpoints for the Kafka ingestion pipeline.
- Parses PDF through MinerU with OCR, Word by heading, PowerPoint by slide, and Excel by sheet/header-aware row windows.
- Parses PNG and JPEG assets with decoder limits, EXIF orientation normalization, normalized-asset hashing, replaceable OCR, and optional VLM enrichment.
- Preserves evidence IDs, document versions, parser receipts, page/slide/sheet locations, image dimensions, and pixel `bbox` metadata.
- Propagates `X-Trace-ID` across Go/Python calls.

## Requirements

- Python 3.11+
- A running RHA Go service
- Elasticsearch for ingestion
- MinerU for production PDF parsing
- An OCR HTTP service for searchable image ingestion
- OpenAI-compatible LLM and embedding endpoints

Install the service dependencies in an isolated environment:

```powershell
python -m venv ai-orchestrator\.venv
ai-orchestrator\.venv\Scripts\python.exe -m pip install -r ai-orchestrator\requirements.txt
```

## Configuration

Copy `ai-orchestrator/.env.example` into your secret-management workflow and provide values as environment variables. Do not commit real API keys or internal tokens.

Core ingestion settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `RHA_INGESTION_MODE` | `production` uses real parsers; `fixture` is test-only | `production` |
| `RHA_MINERU_COMMAND` | MinerU executable used for PDF OCR parsing | `mineru` |
| `RHA_MINERU_TIMEOUT_SECONDS` | PDF parser deadline | `120` |
| `RHA_ES_URL` | Elasticsearch endpoint | `http://127.0.0.1:9200` |

Image ingestion settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `RHA_IMAGE_ALLOWED_MIME_TYPES` | Decoder-verified image MIME allowlist | `image/png,image/jpeg` |
| `RHA_IMAGE_MAX_BYTES` | Maximum encoded source size | `20971520` |
| `RHA_IMAGE_MAX_PIXELS` | Maximum decoded width x height | `40000000` |
| `RHA_IMAGE_OCR_URL` | OCR receipt endpoint; required unless VLM or textless mode supplies evidence | empty |
| `RHA_IMAGE_OCR_TIMEOUT_SECONDS` | OCR request deadline | `30` |
| `RHA_IMAGE_ALLOW_TEXTLESS` | Permit metadata-only image evidence | `false` |
| `RHA_IMAGE_VLM_ENABLED` | Add an OpenAI-compatible full-image summary | `false` |
| `RHA_IMAGE_VLM_BASE_URL` | OpenAI-compatible VLM API root including `/v1` | LLM base URL |
| `RHA_IMAGE_VLM_API_KEY` | Runtime-only VLM credential | LLM API key |
| `RHA_IMAGE_VLM_MODEL` | Vision-capable model name | empty |
| `RHA_IMAGE_VLM_TIMEOUT_SECONDS` | VLM request deadline | `45` |
| `RHA_IMAGE_VLM_MAX_TOKENS` | VLM output limit | `384` |

The OCR endpoint receives normalized pixels as base64 plus `mimeType`, `width`, `height`, and `assetSha256`. It returns an engine/version receipt and text regions:

```json
{
  "engine": "paddleocr-service",
  "version": "3.0",
  "regions": [
    {
      "text": "Valve A17 is closed",
      "bbox": [24, 30, 280, 78],
      "confidence": 0.99
    }
  ]
}
```

Each region must use pixel coordinates inside the normalized image. Invalid MIME, corrupt images, oversized payloads, out-of-range boxes, and empty OCR/VLM results are rejected by default.

## Run

```powershell
$env:RHA_INTERNAL_TOKEN="replace-with-a-private-token"
$env:RHA_GO_BASE_URL="http://127.0.0.1:8081"
$env:RHA_LLM_API_KEY="replace-with-your-llm-key"
ai-orchestrator\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir ai-orchestrator --host 0.0.0.0 --port 8090
```

Enable the client in `configs/config.yaml`:

```yaml
ai:
  orchestrator:
    enabled: true
    ingestion_enabled: true
    base_url: "http://127.0.0.1:8090"
    timeout_ms: 120000
    ingestion_timeout_ms: 180000
    shared_secret: "replace-with-the-same-private-token"
```

## Internal API

Go calls these Python-only worker endpoints:

- `POST /v1/ingestion/parse`
- `POST /v1/ingestion/chunk`
- `POST /v1/ingestion/embed`
- `POST /v1/ingestion/index`
- `POST /v1/memory/summarize`
- `POST /v1/memory/extract`

The orchestrator calls these Go-only endpoints:

- `POST /internal/orchestrator/session`
- `POST /internal/orchestrator/prompt-context`
- `POST /internal/orchestrator/knowledge-search`
- `POST /internal/orchestrator/memory-search`
- `POST /internal/orchestrator/rerank-context`
- `POST /internal/orchestrator/persist`

All internal requests require `X-Internal-Token`; requests and responses carry `X-Trace-ID` for cross-service correlation.

## Tests

```powershell
$env:PYTHONPATH="ai-orchestrator"
python -m unittest discover -s ai-orchestrator/tests -v
```

The repository-level Docker acceptance test is documented in `docs/rha-e2e-runbook.md`.

# RHA Repository Evidence Baseline

This file records what is verified in the repository today. It separates code contracts and local tests from runtime evidence so that a fixture or unit suite is not presented as a production claim.

## Verified now

| Area | Evidence | Boundary |
| --- | --- | --- |
| Go services | `go test ./...` | Unit and repository/service contract coverage; external services are not all exercised. |
| Python orchestrator | `PYTHONPATH=ai-orchestrator python -m unittest discover -s ai-orchestrator/tests -v` | Parser, citation, and memory tests; not a full runtime deployment. |
| Multimodal contract | `python scripts/verify_rha_fixture.py` | PDF, Word, PowerPoint, and Excel; 8 deterministic evidence units. |
| LangGraph topology | `ai-orchestrator/app/graph.py` and orchestrator tests | The graph contains 11 online nodes; runtime model calls still require a configured service or stub. |
| Historical upload benchmark | `benchmarks/results/upload-baguwen-benchmark.json` when available | 120/120 upload success and merge P95 2444.6 ms. The separate upload-to-searchable artifact records `searchable_rate=0.0`; neither result proves current runtime quality. |
| Runtime RHA E2E | `scripts/run_rha_e2e.sh`, `scripts/rha_runtime_e2e.py`, and `scripts/verify_rha_e2e.py` | Isolated Compose execution through the production ingestion mode with deterministic local model, OCR, and MinerU test services. The schema-v4 gate requires 57 located, versioned evidence units across PDF, Word, PowerPoint, Excel, and image; interrupted upload resume; four Kafka stages; alias switch/readback/rollback; permission filtering; cited WebSocket answers; durable memory; and one-task DLQ replay without duplicate knowledge or evidence. |
| Runtime integrity binding | `scripts/verify_rha_release.py --e2e-report <report>` | Requires a sidecar whose report and runner SHA-256 digests match. This detects changed bytes but does not authenticate origin; release evidence still requires a fresh Docker run. |
| Secret hygiene | `python scripts/scan_repository_secrets.py --tracked-only` | Rejects provider keys, private-key material, JWT-like values, and generic credential assignments while allowing documented environment placeholders. Tracked runtime configuration contains placeholders rather than usable credentials. |

## Evidence boundaries

The runtime lane is deterministic acceptance evidence, not proof of external-model quality, production traffic, or throughput. Its PDF parser, OCR endpoint, embedding model, reranker, and chat model are executable local test services; the production parser and network contracts are exercised, but the service outputs remain controlled.

Performance reports must state corpus, model mode, index, concurrency, denominator, and environment. Upload, merge, pipeline completion, searchable rate, citation rate, and answer latency are separate measurements; improving one cannot hide a regression in another.

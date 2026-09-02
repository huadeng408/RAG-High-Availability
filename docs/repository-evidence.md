# RHA Repository Evidence Baseline

This file records what is verified in the repository today. It separates code contracts and local tests from runtime evidence so that a fixture or unit suite is not presented as a production claim.

## Verified now

| Area | Evidence | Boundary |
| --- | --- | --- |
| Go services | `go test ./...` | Unit and repository/service contract coverage; external services are not all exercised. |
| Python orchestrator | `PYTHONPATH=ai-orchestrator python -m unittest discover -s ai-orchestrator/tests -v` | Parser, citation, and memory tests; not a full runtime deployment. |
| Multimodal contract | `python scripts/verify_rha_fixture.py` | PDF, Word, PowerPoint, and Excel; 8 deterministic evidence units. |
| LangGraph topology | `ai-orchestrator/app/graph.py` and orchestrator tests | The graph contains 11 online nodes; runtime model calls still require a configured service or stub. |
| Historical upload benchmark | `benchmarks/results/upload-baguwen-benchmark.json` when available | 120/120 upload success and merge P95 about 2.445 seconds; this does not prove parse, indexing, searchable rate, or cited answers. |
| Existing RHA E2E script | `scripts/run_rha_e2e.sh` and `scripts/verify_rha_e2e.py` | The current report is fixture-backed and validates the citation contract; it is not yet the real upload-to-chat runtime gate. |
| Secret hygiene | `python scripts/scan_repository_secrets.py --tracked-only` | Rejects provider key formats and private-key material in tracked files; local sample credentials still need environment migration. |

## Completion evidence still required

The platform goal remains open until an isolated runtime test performs authenticated chunk upload, repeated-chunk idempotency, merge, all four Kafka stages, real parser output, Elasticsearch alias readback, permission-filtered hybrid search, WebSocket streaming with a citation, and DLQ replay without duplicate evidence. Image ingestion must be included in that path or in a separately linked runtime test.

Performance reports must state corpus, model mode, index, concurrency, denominator, and environment. Upload, merge, pipeline completion, searchable rate, citation rate, and answer latency are separate measurements; improving one cannot hide a regression in another.

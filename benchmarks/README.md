# RHA evaluation artifacts

This directory contains only small, sanitized artifacts needed to reproduce the RHA evaluation contract:

- `schemas/rha-evaluation.schema.json`: Draft 2020-12 JSON Schema for offline evaluation reports.
- `examples/rha-evaluation-fixture.json`: deterministic contract fixture. Its values test metric recomputation; they are not production quality claims.
- `baselines/rha-evaluation-baseline.json`: regression floor for the deterministic fixture lane.
- `baselines/upload-merge-120-summary.json`: sanitized historical upload/merge evidence.

Local corpora and generated results belong under `benchmarks/datasets/`, `benchmarks/results/`, or `benchmarks/tmp/`. Those directories stay ignored because they may be large, machine-specific, or contain runtime context.

## Evidence classes

| Class | What it establishes |
| --- | --- |
| `contract-fixture` | Schema, denominator, and metric-recomputation behavior only. |
| `offline-evaluation` | Retrieval quality for the declared corpus/model/index/environment only. |
| `historical-local-benchmark` | The explicitly stated historical measurements only. |
| schema-v4 runtime E2E | Upload, Kafka processing, production parser routes, indexing, retrieval, permission, WebSocket citation, memory, degradation, alias, and DLQ replay behavior in the Docker test environment. |

The 120-document summary records 120/120 successful uploads, success rate 1.0, and merge P95 2444.6 ms. Its source artifact SHA-256 is recorded in the summary. It does not prove pipeline completion, searchability, citation quality, production scale, or retrieval quality. No historical retrieval report is used as a quality baseline.

## Validate

```powershell
python -m unittest scripts.tests.test_verify_rha_release -v
python scripts/verify_rha_release.py `
  --evaluation-report benchmarks/examples/rha-evaluation-fixture.json `
  --baseline benchmarks/baselines/rha-evaluation-baseline.json `
  --e2e-report $env:RHA_E2E_REPORT
```

The release verifier always requires a separate schema-v4 runtime report. A fixture cannot satisfy that gate.

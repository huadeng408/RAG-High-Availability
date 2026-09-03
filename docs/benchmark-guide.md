# RHA benchmark reproducibility guide

RHA reports retrieval quality, latency, pipeline rates, and runtime acceptance as separate evidence. Results are comparable only when corpus, qrels, model identities, index settings, hardware/runtime, concurrency, and `topK` are held constant.

## Offline evaluation contract

An offline report must conform to [`benchmarks/schemas/rha-evaluation.schema.json`](../benchmarks/schemas/rha-evaluation.schema.json). Start from [`benchmarks/examples/rha-evaluation-fixture.json`](../benchmarks/examples/rha-evaluation-fixture.json), but replace every fixture identity, query, qrel, ranked hit, latency sample, and rate with observations from the evaluated environment.

Every report records:

- full commit SHA and UTC generation time;
- corpus ID/SHA-256, query count, and qrel count;
- explicit model mode: `named` requires LLM, embedding, and reranker identities; `deterministic` may omit them;
- index alias, analyzer, vector dimensions, and distance function;
- hardware/runtime, concurrency, and `topK`;
- per-query qrels, ranked hits, hit count, and citation count;
- raw latency samples and P50/P95/P99;
- separate upload, merge, pipeline, searchable, and cited-answer rates.

Rates always include an integer numerator and denominator. Do not infer a searchable rate from uploads, or a cited-answer rate from retrieval hits.

## Metric denominators

- `Recall@K = relevant qrels found in the first K hits / all relevant qrels`. The repository contract aggregates over qrels, not over queries.
- `MRR = sum(first relevant reciprocal rank per query) / query count`.
- `nDCG@K = sum(per-query DCG@K / ideal DCG@K) / query count`, using graded relevance and `2^relevance - 1` gain.
- Percentiles use linear interpolation over sorted raw latency samples. `sampleCount` must equal the sample array length.

The release verifier recomputes these values from qrels and ranked hits. Self-declared booleans or aggregates are not accepted as proof.

## Produce an evaluation

Run the same corpus and qrels against a fully declared environment. Keep credentials in environment variables and write generated reports to the ignored results directory:

```powershell
$env:RHA_BASE_URL="https://rha.example.invalid/api/v1"
$env:RHA_USERNAME="<benchmark-user>"
$env:RHA_PASSWORD="<benchmark-password>"
$env:RHA_EVALUATION_OUT="benchmarks/results/rha-offline-evaluation.json"

# Run your retrieval driver, then validate the resulting structured report.
python scripts/verify_rha_release.py `
  --evaluation-report $env:RHA_EVALUATION_OUT `
  --baseline <matching-environment-baseline.json> `
  --e2e-report $env:RHA_E2E_REPORT
```

For comparisons, preserve the exact corpus hash, qrels, model versions, index/analyzer/vector configuration, hardware/runtime, concurrency, and `topK`. The verifier rejects a baseline whose declared environment differs. If any setting changes, report the runs separately rather than claiming a regression or improvement. The checked-in quality baseline applies only to the deterministic fixture lane.

## Runtime release evidence

Offline evaluation does not replace runtime acceptance. Generate a fresh Docker-backed schema-v4 report with deterministic model inference:

```powershell
$env:RHA_E2E_REPORT = Join-Path $env:TEMP "rha-runtime-e2e.json"
bash scripts/run_rha_e2e.sh
python scripts/verify_rha_release.py --e2e-report $env:RHA_E2E_REPORT
```

The runtime lane invokes the existing E2E verifier and requires real HTTP upload, Kafka four-stage processing and replay, production parser routes, Elasticsearch alias readback and retrieval, permission filtering, WebSocket trace/citation evidence, durable memory, and degradation behavior. Deterministic model/OCR services make inference repeatable; they do not make the report a production performance or answer-quality benchmark.

## Release gate

```powershell
go test ./...
$env:PYTHONPATH="ai-orchestrator"
python -m unittest discover -s ai-orchestrator/tests -v
python -m unittest discover -s scripts/tests -v
python scripts/scan_repository_secrets.py --tracked-only
python scripts/verify_rha_release.py --e2e-report $env:RHA_E2E_REPORT
git diff --check
```

Generated reports, local corpora, credentials, databases, binaries, caches, and machine-specific paths must remain untracked.

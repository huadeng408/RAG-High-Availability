#!/usr/bin/env python3
"""Verify RHA offline evaluation evidence and the runtime release gate."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALUATION = ROOT / "benchmarks" / "examples" / "rha-evaluation-fixture.json"
DEFAULT_BASELINE = ROOT / "benchmarks" / "baselines" / "rha-evaluation-baseline.json"
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
BASELINE_PARITY_FIELDS = ("corpus", "model", "index", "environment", "concurrency", "topK")
FORBIDDEN_BENCHMARK_PREFIXES = ("benchmarks/results/", "benchmarks/datasets/", "benchmarks/tmp/")
FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".aof", ".exe", ".dll", ".so", ".bin",
    ".pkl", ".pickle", ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".zip", ".tar",
    ".tgz", ".gz", ".7z", ".rar",
}
FORBIDDEN_FILENAMES = {"credentials.json", "secrets.json", "secrets.env", ".env", ".env.local", "id_rsa", "id_ed25519"}
ALLOWED_TRACKED_CONFIGS = {"ai-orchestrator/.env.example", "frontend/.env", "frontend/.env.prod", "frontend/.env.test"}
ALLOWED_TRACKED_ASSETS = {"frontend/src/assets/svg-icon/文件类型图标.zip"}
ALLOWED_CURATED_ARTIFACTS = {
    "benchmarks/baselines/rha-evaluation-baseline.json",
    "benchmarks/baselines/upload-merge-120-summary.json",
    "benchmarks/examples/rha-evaluation-fixture.json",
    "benchmarks/schemas/rha-evaluation.schema.json",
}
METRIC_NAMES = ("recallAtK", "mrr", "ndcgAtK")
RATE_NAMES = ("upload", "merge", "pipeline", "searchable", "citedAnswer")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _digest(value: Any, field: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _validate_utc_timestamp(value: Any, field: str) -> str:
    text = str(value)
    if not RFC3339_UTC.fullmatch(text):
        raise ValueError(f"{field} must be a UTC RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC RFC 3339 timestamp") from exc
    if parsed.tzinfo != timezone.utc or datetime.fromisoformat(parsed.isoformat()) != parsed:
        raise ValueError(f"{field} must round-trip as UTC RFC 3339")
    return text


def _validate_model(value: Any, field: str) -> dict[str, Any]:
    model = _require_object(value, field)
    mode = model.get("mode")
    if mode not in {"deterministic", "named"}:
        raise ValueError(f"{field}.mode must be deterministic or named")
    for name in ("llm", "embedding", "reranker"):
        identity = model.get(name)
        if mode == "named" and not isinstance(identity, str):
            raise ValueError(f"{field}.{name} is required in named mode")
        if identity is not None and (not isinstance(identity, str) or not identity.strip()):
            raise ValueError(f"{field}.{name} must be a non-empty string when provided")
    return model


def _percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("latency.samplesMs must not be empty")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _validate_metric(metric: dict[str, Any], expected_value: float, expected_num: float, expected_den: int, name: str) -> None:
    value = _number(metric.get("value"), f"metrics.{name}.value")
    numerator = _number(metric.get("numerator"), f"metrics.{name}.numerator")
    denominator = _integer(metric.get("denominator"), f"metrics.{name}.denominator")
    if denominator != expected_den or abs(numerator - expected_num) > 1e-9 or abs(value - expected_value) > 1e-6:
        raise ValueError(f"metrics.{name} aggregate does not match recomputed numerator/denominator")


def verify_offline_report(report: dict[str, Any], *, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate an offline report and recompute all declared quality metrics."""
    if report.get("reportKind") != "rha-offline-evaluation":
        raise ValueError("reportKind must identify an offline evaluation report")
    if report.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    if report.get("evidenceClass") not in {"contract-fixture", "offline-evaluation"}:
        raise ValueError("evidenceClass must identify contract-fixture or offline-evaluation evidence")
    _validate_utc_timestamp(report.get("generatedAt"), "generatedAt")
    commit = str(report.get("commit", ""))
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError("commit must be a full lowercase Git SHA")
    corpus = _require_object(report.get("corpus"), "corpus")
    if not str(corpus.get("id", "")).strip():
        raise ValueError("corpus.id is required")
    _digest(corpus.get("sha256"), "corpus.sha256")
    _validate_model(report.get("model"), "model")
    index = _require_object(report.get("index"), "index")
    if not str(index.get("alias", "")).strip() or not str(index.get("analyzer", "")).strip():
        raise ValueError("index alias and analyzer are required")
    vector = _require_object(index.get("vector"), "index.vector")
    if _integer(vector.get("dimensions"), "index.vector.dimensions") < 1 or not str(vector.get("distance", "")).strip():
        raise ValueError("index.vector dimensions and distance are required")
    environment = _require_object(report.get("environment"), "environment")
    if not str(environment.get("hardware", "")).strip() or not str(environment.get("runtime", "")).strip():
        raise ValueError("environment hardware and runtime are required")
    if _integer(report.get("concurrency"), "concurrency") < 1:
        raise ValueError("concurrency must be positive")
    queries = report.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("queries must be a non-empty array")
    query_count = _integer(corpus.get("queryCount"), "corpus.queryCount")
    qrel_count = _integer(corpus.get("qrelCount"), "corpus.qrelCount")
    if query_count != len(queries):
        raise ValueError("corpus.queryCount must equal queries length")
    actual_qrel_count = 0
    relevant_retrieved = 0
    reciprocal_sum = 0.0
    ndcg_sum = 0.0
    top_k = _integer(report.get("topK"), "topK")
    if top_k < 1:
        raise ValueError("topK must be positive")
    for index, query in enumerate(queries):
        query = _require_object(query, f"queries[{index}]")
        if not str(query.get("queryId", "")).strip():
            raise ValueError(f"queries[{index}].queryId is required")
        qrels = query.get("qrels")
        hits = query.get("rankedHits")
        if not isinstance(qrels, list) or not qrels:
            raise ValueError(f"queries[{index}].qrels must be non-empty")
        if not isinstance(hits, list):
            raise ValueError(f"queries[{index}].rankedHits must be an array")
        relevance: dict[str, float] = {}
        for qrel in qrels:
            qrel = _require_object(qrel, f"queries[{index}].qrels")
            evidence_id = str(qrel.get("evidenceId", "")).strip()
            if not evidence_id or evidence_id in relevance:
                raise ValueError(f"queries[{index}].qrels contains duplicate or empty evidenceId")
            grade = _integer(qrel.get("relevance"), f"queries[{index}].qrels.relevance")
            if grade < 1:
                raise ValueError("qrel relevance must be positive")
            relevance[evidence_id] = float(grade)
        actual_qrel_count += len(relevance)
        ranked_ids: list[str] = []
        for hit in hits:
            hit = _require_object(hit, f"queries[{index}].rankedHits")
            evidence_id = str(hit.get("evidenceId", "")).strip()
            if not evidence_id or evidence_id in ranked_ids:
                raise ValueError(f"queries[{index}].rankedHits contains duplicate or empty evidenceId")
            _number(hit.get("score"), f"queries[{index}].rankedHits.score")
            ranked_ids.append(evidence_id)
        if _integer(query.get("hitCount"), f"queries[{index}].hitCount") != len(ranked_ids):
            raise ValueError(f"queries[{index}].hitCount must equal rankedHits length")
        top_hits = ranked_ids[:top_k]
        relevant_ids = {evidence_id for evidence_id, grade in relevance.items() if grade > 0}
        relevant_retrieved += len(relevant_ids.intersection(top_hits))
        first_rank = next((rank for rank, evidence_id in enumerate(top_hits, 1) if evidence_id in relevant_ids), None)
        reciprocal_sum += 0.0 if first_rank is None else 1.0 / first_rank
        dcg = sum((2 ** relevance[evidence_id] - 1) / math.log2(rank + 1) for rank, evidence_id in enumerate(top_hits, 1) if evidence_id in relevance and relevance[evidence_id] > 0)
        ideal = sorted((grade for grade in relevance.values() if grade > 0), reverse=True)[:top_k]
        idcg = sum((2 ** grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
        ndcg_sum += dcg / idcg if idcg else 0.0
        citation_count = _integer(query.get("citationCount"), f"queries[{index}].citationCount")
        if citation_count < 0:
            raise ValueError("citationCount must be non-negative")
    if qrel_count != actual_qrel_count:
        raise ValueError("corpus.qrelCount must equal per-query qrels")
    metrics = _require_object(report.get("metrics"), "metrics")
    _validate_metric(_require_object(metrics.get("recallAtK"), "metrics.recallAtK"), relevant_retrieved / qrel_count, relevant_retrieved, qrel_count, "recallAtK")
    _validate_metric(_require_object(metrics.get("mrr"), "metrics.mrr"), reciprocal_sum / query_count, reciprocal_sum, query_count, "mrr")
    _validate_metric(_require_object(metrics.get("ndcgAtK"), "metrics.ndcgAtK"), ndcg_sum / query_count, ndcg_sum, query_count, "ndcgAtK")

    latency = _require_object(report.get("latency"), "latency")
    samples = latency.get("samplesMs")
    if not isinstance(samples, list) or not samples:
        raise ValueError("latency.samplesMs must be a non-empty array")
    sample_values = [_number(value, "latency.samplesMs") for value in samples]
    if _integer(latency.get("sampleCount"), "latency.sampleCount") != len(sample_values):
        raise ValueError("latency.sampleCount must equal samplesMs length")
    percentiles = _require_object(latency.get("percentilesMs"), "latency.percentilesMs")
    for name, quantile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        declared = _number(percentiles.get(name), f"latency.percentilesMs.{name}")
        if abs(declared - _percentile(sample_values, quantile)) > 1e-6:
            raise ValueError(f"latency.percentilesMs.{name} does not match samplesMs")
    rates = _require_object(report.get("rates"), "rates")
    for name in RATE_NAMES:
        rate = _require_object(rates.get(name), f"rates.{name}")
        numerator = _integer(rate.get("numerator"), f"rates.{name}.numerator")
        denominator = _integer(rate.get("denominator"), f"rates.{name}.denominator")
        value = _number(rate.get("value"), f"rates.{name}.value")
        if denominator <= 0 or numerator < 0 or numerator > denominator or abs(value - numerator / denominator) > 1e-9:
            raise ValueError(f"rates.{name} has invalid numerator/denominator")
    if baseline is not None:
        if baseline.get("reportKind") != "rha-offline-quality-baseline":
            raise ValueError("baseline.reportKind must identify an offline quality baseline")
        if baseline.get("schemaVersion") != 1:
            raise ValueError("baseline.schemaVersion must be 1")
        if baseline.get("evidenceClass") not in {"contract-fixture", "offline-evaluation"}:
            raise ValueError("baseline.evidenceClass is invalid")
        for field in BASELINE_PARITY_FIELDS:
            if field not in baseline:
                raise ValueError(f"baseline.{field} is required for parity")
        for field in ("corpus", "index", "environment"):
            _require_object(baseline.get(field), f"baseline.{field}")
        _validate_model(baseline.get("model"), "baseline.model")
        if _integer(baseline.get("concurrency"), "baseline.concurrency") < 1:
            raise ValueError("baseline.concurrency must be positive")
        if _integer(baseline.get("topK"), "baseline.topK") < 1:
            raise ValueError("baseline.topK must be positive")
        for field in BASELINE_PARITY_FIELDS:
            if report.get(field) != baseline.get(field):
                raise ValueError(f"baseline parity mismatch: {field}")
        baseline_metrics = _require_object(baseline.get("metrics"), "baseline.metrics")
        for name in METRIC_NAMES:
            current = _number(_require_object(metrics.get(name), f"metrics.{name}").get("value"), f"metrics.{name}.value")
            minimum = _number(_require_object(baseline_metrics.get(name), f"baseline.metrics.{name}").get("value"), f"baseline.metrics.{name}.value")
            if current + 1e-9 < minimum:
                raise ValueError(f"metric regression: {name} {current} < baseline {minimum}")
    return report


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load JSON {path}: {exc}") from exc
    return _require_object(value, str(path))


def run_runtime_verifier(path: Path) -> None:
    command = [sys.executable, str(ROOT / "scripts" / "verify_rha_e2e.py"), "--report", str(path)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stdout or completed.stderr).strip()
        raise ValueError(f"runtime E2E verification failed: {detail}")


def _contains_fixture_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_fixture_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_fixture_marker(item) for item in value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "-")
        return normalized in {"fixture", "fixture-only", "contract-fixture", "synthetic"}
    return False


def verify_runtime_report(path: Path) -> dict[str, Any]:
    report = load_json(path)
    if report.get("reportKind") != "rha-runtime-e2e":
        raise ValueError("runtime E2E reportKind is required")
    if report.get("schemaVersion") != 4:
        raise ValueError("runtime E2E report must use schemaVersion 4")
    if not isinstance(report.get("reliability"), dict):
        raise ValueError("runtime E2E reliability evidence is required")
    if _contains_fixture_marker(report):
        raise ValueError("fixture/synthetic report cannot be presented as runtime E2E")
    run_runtime_verifier(path)
    return report


def _tracked_path_violation(item: str) -> bool:
    normalized = item.replace("\\", "/").lower()
    if normalized in ALLOWED_TRACKED_CONFIGS or normalized in ALLOWED_TRACKED_ASSETS or normalized in ALLOWED_CURATED_ARTIFACTS:
        return False
    path = Path(normalized)
    if normalized.startswith(FORBIDDEN_BENCHMARK_PREFIXES):
        return True
    if path.suffix in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_FILENAMES or path.name.startswith(".env."):
        return True
    tokens = {token for token in re.split(r"[^a-z0-9]+", path.stem) if token}
    if tokens.intersection({"secret", "secrets", "password", "passwords", "credential", "credentials"}) and path.suffix.lower() not in {".py", ".go", ".md", ".html", ".ts", ".tsx", ".js", ".sh"}:
        return True
    if path.suffix.lower() in {".json", ".jsonl", ".xml", ".yaml", ".yml"} and ("report" in tokens or "/reports/" in normalized):
        return True
    return False


def verify_tracked_policy() -> None:
    completed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ValueError("unable to inspect tracked files")
    paths = [item for item in completed.stdout.decode("utf-8", errors="strict").split("\0") if item]
    suspicious = []
    for item in paths:
        if _tracked_path_violation(item):
            suspicious.append(item)
    if suspicious:
        raise ValueError("tracked runtime/secret/binary/database paths: " + ", ".join(suspicious))


def run_secret_scan() -> None:
    command = [sys.executable, str(ROOT / "scripts" / "scan_repository_secrets.py"), "--tracked-only"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stdout or completed.stderr).strip()
        raise ValueError(f"tracked secret scan failed: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e2e-report", type=Path, required=True)
    parser.add_argument("--evaluation-report", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()
    try:
        evaluation = load_json(args.evaluation_report)
        baseline = load_json(args.baseline)
        verify_offline_report(evaluation, baseline=baseline)
        verify_runtime_report(args.e2e_report)
        verify_tracked_policy()
        run_secret_scan()
    except ValueError as exc:
        print(f"RHA release verification failed: {exc}", file=sys.stderr)
        return 1
    print("RHA release verified: offline metrics, runtime schema-v4 E2E, tracked artifact policy, and secret scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

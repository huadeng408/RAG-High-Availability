#!/usr/bin/env python3
"""Verify RHA offline evaluation evidence and the runtime release gate."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALUATION = ROOT / "benchmarks" / "examples" / "rha-evaluation-fixture.json"
DEFAULT_BASELINE = ROOT / "benchmarks" / "baselines" / "rha-evaluation-baseline.json"
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".aof", ".exe", ".dll", ".so", ".bin", ".pkl", ".pickle"}
FORBIDDEN_FILENAMES = {"credentials.json", "secrets.json", "secrets.env", ".env.local"}
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
    generated_at = str(report.get("generatedAt", ""))
    if not generated_at.endswith("Z") or "T" not in generated_at:
        raise ValueError("generatedAt must be a UTC RFC 3339 timestamp")
    commit = str(report.get("commit", ""))
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError("commit must be a full lowercase Git SHA")
    corpus = _require_object(report.get("corpus"), "corpus")
    if not str(corpus.get("id", "")).strip():
        raise ValueError("corpus.id is required")
    _digest(corpus.get("sha256"), "corpus.sha256")
    model = _require_object(report.get("model"), "model")
    if model.get("mode") not in {"deterministic", "external"}:
        raise ValueError("model.mode must be deterministic or external")
    for name in ("llm", "embedding", "reranker"):
        if not str(model.get(name, "")).strip():
            raise ValueError(f"model.{name} is required")
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
        for field in ("corpus", "model", "index", "environment", "concurrency", "topK"):
            if field in baseline and report.get(field) != baseline.get(field):
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
    metadata = {key: report.get(key) for key in ("evidenceClass", "mode", "environment", "ingestionMode", "parserMode")}
    if _contains_fixture_marker(metadata):
        raise ValueError("fixture/synthetic report cannot be presented as runtime E2E")
    run_runtime_verifier(path)
    return report


def verify_tracked_policy() -> None:
    completed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ValueError("unable to inspect tracked files")
    paths = [item for item in completed.stdout.decode("utf-8", errors="strict").split("\0") if item]
    suspicious = []
    for item in paths:
        normalized = item.replace("\\", "/").lower()
        suffix = Path(normalized).suffix
        if normalized.startswith("benchmarks/results/") or suffix in FORBIDDEN_SUFFIXES or Path(normalized).name in FORBIDDEN_FILENAMES:
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

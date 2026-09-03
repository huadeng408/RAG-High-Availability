from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_rha_release.py"
SPEC = importlib.util.spec_from_file_location("verify_rha_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def evaluation_report() -> dict:
    return {
        "reportKind": "rha-offline-evaluation",
        "schemaVersion": 1,
        "evidenceClass": "contract-fixture",
        "generatedAt": "2026-09-03T00:00:00Z",
        "commit": "e0eed22c2f42fdba9113804529dbf84b3c904984",
        "corpus": {"id": "rha-fixture", "sha256": "a" * 64, "queryCount": 2, "qrelCount": 3},
        "model": {"mode": "deterministic", "llm": "fixture-llm", "embedding": "fixture-embedding", "reranker": "fixture-reranker"},
        "index": {"alias": "rha-knowledge-active", "analyzer": "standard", "vector": {"dimensions": 8, "distance": "cosine"}},
        "environment": {"hardware": "local-ci", "runtime": "python3.11"},
        "concurrency": 1,
        "topK": 2,
        "queries": [
            {"queryId": "q1", "qrels": [{"evidenceId": "a", "relevance": 3}, {"evidenceId": "b", "relevance": 1}], "rankedHits": [{"evidenceId": "a", "score": 1.0}, {"evidenceId": "x", "score": 0.2}], "hitCount": 2, "citationCount": 1},
            {"queryId": "q2", "qrels": [{"evidenceId": "c", "relevance": 2}], "rankedHits": [{"evidenceId": "x", "score": 0.9}, {"evidenceId": "c", "score": 0.8}], "hitCount": 2, "citationCount": 1},
        ],
        "metrics": {"recallAtK": {"value": 2 / 3, "numerator": 2, "denominator": 3}, "mrr": {"value": 0.75, "numerator": 1.5, "denominator": 2}, "ndcgAtK": {"value": 0.7741245831422072, "numerator": 1.5482491662844144, "denominator": 2}},
        "latency": {"samplesMs": [10, 20, 30, 40], "sampleCount": 4, "percentilesMs": {"p50": 25.0, "p95": 38.5, "p99": 39.7}},
        "rates": {name: {"numerator": 2, "denominator": 2, "value": 1.0} for name in ("upload", "merge", "pipeline", "searchable", "citedAnswer")},
    }


class VerifyRhaReleaseTest(unittest.TestCase):
    def test_checked_in_example_conforms_to_evaluation_schema(self) -> None:
        schema = json.loads((ROOT / "benchmarks" / "schemas" / "rha-evaluation.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "benchmarks" / "examples" / "rha-evaluation-fixture.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(set(schema["required"]), set(example))
        MODULE.verify_offline_report(example, baseline=example)

    def test_sanitized_upload_baseline_keeps_only_the_verified_floor(self) -> None:
        summary = json.loads((ROOT / "benchmarks" / "baselines" / "upload-merge-120-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["upload"], {"numerator": 120, "denominator": 120, "successRate": 1.0})
        self.assertEqual(summary["mergeLatencyMs"]["p95"], 2444.6)
        self.assertRegex(summary["sourceArtifactSha256"], r"^[0-9a-f]{64}$")
        for raw_field in ("results", "docs_dir", "user", "generated_at"):
            self.assertNotIn(raw_field, summary)

    def test_recomputes_metrics_and_accepts_valid_fixture(self) -> None:
        result = MODULE.verify_offline_report(evaluation_report(), baseline=evaluation_report())
        self.assertAlmostEqual(result["metrics"]["mrr"]["value"], 0.75)

    def test_rejects_declared_metric_mismatch(self) -> None:
        report = evaluation_report()
        report["metrics"]["mrr"]["value"] = 1.0
        with self.assertRaisesRegex(ValueError, "mrr"):
            MODULE.verify_offline_report(report, baseline=report)

    def test_rejects_missing_execution_metadata(self) -> None:
        for field in ("model", "index", "environment", "concurrency"):
            report = evaluation_report()
            del report[field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                MODULE.verify_offline_report(report, baseline=report)

    def test_rejects_hit_count_mismatch(self) -> None:
        report = evaluation_report()
        report["queries"][0]["hitCount"] = 1
        with self.assertRaisesRegex(ValueError, "hitCount"):
            MODULE.verify_offline_report(report, baseline=report)

    def test_rejects_latency_denominator_mismatch(self) -> None:
        report = evaluation_report()
        report["latency"]["sampleCount"] = 3
        with self.assertRaisesRegex(ValueError, "sampleCount"):
            MODULE.verify_offline_report(report, baseline=report)

    def test_rejects_rate_denominator_mismatch(self) -> None:
        report = evaluation_report()
        report["rates"]["searchable"]["value"] = 0.5
        with self.assertRaisesRegex(ValueError, "searchable"):
            MODULE.verify_offline_report(report, baseline=report)

    def test_rejects_metric_regression_against_baseline(self) -> None:
        report = evaluation_report()
        baseline = evaluation_report()
        report["queries"][0]["rankedHits"] = [
            {"evidenceId": "x", "score": 1.0},
            {"evidenceId": "y", "score": 0.2},
        ]
        report["metrics"]["recallAtK"]["value"] = 1 / 3
        report["metrics"]["recallAtK"]["numerator"] = 1
        report["metrics"]["mrr"] = {"value": 0.25, "numerator": 0.5, "denominator": 2}
        report["metrics"]["ndcgAtK"] = {"value": 0.3154648768, "numerator": 0.6309297536, "denominator": 2}
        with self.assertRaisesRegex(ValueError, "regression"):
            MODULE.verify_offline_report(report, baseline=baseline)

    def test_rejects_baseline_from_a_different_environment(self) -> None:
        report = evaluation_report()
        baseline = evaluation_report()
        baseline["concurrency"] = 8
        with self.assertRaisesRegex(ValueError, "parity mismatch: concurrency"):
            MODULE.verify_offline_report(report, baseline=baseline)

    def test_runtime_gate_requires_schema_v4_and_rejects_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps({"reportKind": "rha-runtime-e2e", "schemaVersion": 4, "reliability": {}, "evidenceClass": "fixture"}), encoding="utf-8")
            with patch.object(MODULE, "run_runtime_verifier", side_effect=ValueError("fixture runtime report")):
                with self.assertRaisesRegex(ValueError, "fixture/synthetic"):
                    MODULE.verify_runtime_report(path)

    def test_runtime_gate_rejects_nested_fixture_environment_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps({"reportKind": "rha-runtime-e2e", "schemaVersion": 4, "reliability": {}, "environment": {"ingestionMode": "fixture"}}), encoding="utf-8")
            with patch.object(MODULE, "run_runtime_verifier"):
                with self.assertRaisesRegex(ValueError, "fixture/synthetic"):
                    MODULE.verify_runtime_report(path)

    def test_runtime_gate_rejects_pre_v4_report_before_invoking_existing_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps({"reportKind": "rha-runtime-e2e", "schemaVersion": 3}), encoding="utf-8")
            with patch.object(MODULE, "run_runtime_verifier") as verifier:
                with self.assertRaisesRegex(ValueError, "schemaVersion 4"):
                    MODULE.verify_runtime_report(path)
                verifier.assert_not_called()

    def test_secret_scan_failure_is_not_accepted_as_boolean_evidence(self) -> None:
        failed = MODULE.subprocess.CompletedProcess([], 1, stdout="", stderr="secret found")
        with patch.object(MODULE.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(ValueError, "secret found"):
                MODULE.run_secret_scan()

    def test_tracked_runtime_artifact_is_rejected(self) -> None:
        listed = MODULE.subprocess.CompletedProcess([], 0, stdout=b"benchmarks/results/runtime.json\0")
        with patch.object(MODULE.subprocess, "run", return_value=listed):
            with self.assertRaisesRegex(ValueError, "tracked runtime"):
                MODULE.verify_tracked_policy()


if __name__ == "__main__":
    unittest.main()

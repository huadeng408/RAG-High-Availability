from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "verify_rha_e2e.py"
SPEC = importlib.util.spec_from_file_location("verify_rha_e2e", SCRIPT_PATH)
assert SPEC and SPEC.loader
VERIFY_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY_MODULE)


def runtime_report() -> dict:
    citation = {
        "evidenceId": "ppt-slide-1",
        "documentVersion": "content-version",
        "modality": "ppt",
        "slide": 1,
    }
    image_citation = {
        "evidenceId": "image-ocr-1",
        "documentVersion": "image-version",
        "modality": "image",
        "bbox": {"x0": 24, "y0": 30, "x1": 280, "y1": 78},
        "image": {
            "assetSha256": "a" * 64,
            "mimeType": "image/png",
            "width": 320,
            "height": 120,
        },
    }
    report = {
        "reportKind": "rha-runtime-e2e",
        "schemaVersion": 2,
        "traceId": "runtime-trace",
        "auth": {
            "registerStatusCode": 200,
            "loginStatusCode": 200,
            "tokenAcquired": True,
        },
        "upload": {
            "fileMd5": "0123456789abcdef0123456789abcdef",
            "fileName": "rha-runtime.pptx",
            "chunkCount": 1,
            "chunkRequests": [
                {"chunkIndex": 0, "statusCode": 200},
                {"chunkIndex": 0, "statusCode": 200},
            ],
            "merge": {"statusCode": 200},
        },
        "pipeline": {
            "source": "GET /api/v1/documents/pipeline-status",
            "status": "SEARCHABLE",
            "documentVersion": "content-version",
            "alias": "rha-knowledge-active",
            "aliasReadback": {
                "source": "GET /_alias/rha-knowledge-active",
                "statusCode": 200,
                "indices": ["rha-knowledge-v1"],
            },
            "stages": [
                {"stage": "parse", "status": "SUCCESS", "attemptCount": 1},
                {"stage": "chunk", "status": "SUCCESS", "attemptCount": 1},
                {"stage": "embed", "status": "SUCCESS", "attemptCount": 1},
                {"stage": "index", "status": "SUCCESS", "attemptCount": 1},
            ],
        },
        "retrieval": {
            "source": "GET /api/v1/search/hybrid",
            "statusCode": 200,
            "traceId": "runtime-trace",
            "hits": [{"fileMd5": "0123456789abcdef0123456789abcdef", "citations": [dict(citation)]}],
        },
        "websocket": {
            "source": "GET /chat/:token",
            "traceId": "runtime-trace",
            "answer": "The retention period is seven years.",
            "citations": [dict(citation)],
        },
    }
    report["image"] = {
        "upload": {
            "fileMd5": "fedcba9876543210fedcba9876543210",
            "fileName": "rha-runtime.png",
            "chunkCount": 1,
            "chunkRequests": [
                {"chunkIndex": 0, "statusCode": 200},
                {"chunkIndex": 0, "statusCode": 200},
            ],
            "merge": {"statusCode": 200},
        },
        "pipeline": {
            "status": "SEARCHABLE",
            "documentVersion": "image-version",
            "stages": [
                {"stage": stage, "status": "SUCCESS", "attemptCount": 1}
                for stage in ("parse", "chunk", "embed", "index")
            ],
        },
        "retrieval": {
            "statusCode": 200,
            "traceId": "runtime-trace",
            "hits": [{"fileMd5": "fedcba9876543210fedcba9876543210", "citations": [dict(image_citation)]}],
        },
        "websocket": {
            "traceId": "runtime-trace",
            "answer": "The image inspection code is IMG-2048.",
            "citations": [dict(image_citation)],
        },
    }
    report["recovery"] = {
        "upload": {
            "fileMd5": "fedcba9876543210fedcba9876543210",
            "fileName": "rha-recovery.png",
            "chunkCount": 1,
            "chunkRequests": [
                {"chunkIndex": 0, "statusCode": 200},
                {"chunkIndex": 0, "statusCode": 200},
            ],
            "merge": {"statusCode": 200},
        },
        "stage": "embed",
        "dlqMessageId": "d" * 64,
        "dlq": {
            "topic": "file-dlq",
            "messageId": "d" * 64,
            "payload": {
                "stage": "embed",
                "file_md5": "fedcba9876543210fedcba9876543210",
                "document_version": "image-version",
                "dlq_id": "d" * 64,
            },
        },
        "replay": {
            "statusCode": 200,
            "replayedTasks": 1,
            "messageIds": ["d" * 64],
        },
        "pipeline": {
            "status": "SEARCHABLE",
            "documentVersion": "image-version",
            "stages": [
                {"stage": stage, "status": "SUCCESS", "attemptCount": 2, "replayCount": 1}
                for stage in ("parse", "chunk", "embed", "index")
            ],
        },
        "elasticsearch": {"knowledgeCount": 1, "evidenceCount": 1},
    }
    return report


def recovery_report() -> dict:
    report = runtime_report()
    report["schemaVersion"] = 3
    return report


def reliability_report() -> dict:
    report = recovery_report()
    report["schemaVersion"] = 4
    report["reliability"] = {
        "degradation": {"embeddingFailureFallback": True, "rerankerTimeoutFallback": True},
        "permission": {"permittedHit": True, "foreignPrivateAbsent": True, "citationsFiltered": True},
        "memory": {"marker": "RHA-MEMORY-1", "firstTurnStored": True, "secondTurnRetrieved": True, "durable": True, "shortTermHistoryCleared": True},
        "trace": {"events": [{"type": "chunk", "traceId": "runtime-trace"}, {"type": "completion", "traceId": "runtime-trace"}]},
        "graph": {
            "nodes": VERIFY_MODULE.GRAPH_NODES,
            "edges": [[left, right] for left, right in zip(
                ["__start__", *VERIFY_MODULE.GRAPH_NODES, "__end__"],
                ["__start__", *VERIFY_MODULE.GRAPH_NODES, "__end__"][1:],
            )],
        },
    }
    return report


class VerifyRhaE2ETest(unittest.TestCase):
    def test_accepts_schema_v4_reliability_evidence(self) -> None:
        report = reliability_report()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            VERIFY_MODULE.verify(path)

    def test_rejects_schema_v4_when_each_reliability_dimension_is_missing(self) -> None:
        for dimension in ("degradation", "permission", "memory", "trace", "graph"):
            report = reliability_report()
            del report["reliability"][dimension]
            with self.subTest(dimension=dimension), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(ValueError):
                    VERIFY_MODULE.verify(path)
    def test_rejects_report_without_image_runtime_path(self) -> None:
        report = runtime_report()
        del report["image"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_citation_without_pixel_metadata(self) -> None:
        report = runtime_report()
        del report["image"]["websocket"]["citations"][0]["image"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pixel"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_upload_without_duplicate_chunk(self) -> None:
        report = runtime_report()
        report["image"]["upload"]["chunkRequests"] = [{"chunkIndex": 0, "statusCode": 200}]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image.upload.chunkRequests"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_pipeline_without_all_four_successful_stages(self) -> None:
        report = runtime_report()
        report["image"]["pipeline"]["stages"] = report["image"]["pipeline"]["stages"][:-1]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image.pipeline.stages"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_retrieval_from_an_older_upload(self) -> None:
        report = runtime_report()
        report["image"]["retrieval"]["hits"][0]["fileMd5"] = report["upload"]["fileMd5"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image.retrieval.hits.*uploaded file"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_websocket_citation_missing_from_retrieval(self) -> None:
        report = runtime_report()
        report["image"]["websocket"]["citations"][0]["evidenceId"] = "invented-image-evidence"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image.websocket citation.*retrieval citation"):
                VERIFY_MODULE.verify(path)

    def test_rejects_non_image_citation_on_image_path(self) -> None:
        report = runtime_report()
        report["image"]["websocket"]["citations"][0]["modality"] = "text"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modality=image"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_answer_without_the_ocr_fact(self) -> None:
        report = runtime_report()
        report["image"]["websocket"]["answer"] = "The retention period is seven years."

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "IMG-2048"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_citation_with_invalid_bbox(self) -> None:
        report = runtime_report()
        report["image"]["websocket"]["citations"][0]["bbox"]["x1"] = 20

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bbox"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_citation_with_invalid_asset_hash(self) -> None:
        report = runtime_report()
        report["image"]["websocket"]["citations"][0]["image"]["assetSha256"] = "not-a-sha256"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "assetSha256"):
                VERIFY_MODULE.verify(path)

    def test_rejects_image_citation_with_non_image_mime_type(self) -> None:
        report = runtime_report()
        report["image"]["websocket"]["citations"][0]["image"]["mimeType"] = "application/octet-stream"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mimeType"):
                VERIFY_MODULE.verify(path)

    def test_cli_accepts_runtime_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(runtime_report()), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--report", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ppt-slide-1", completed.stdout)

    def test_accepts_slide_level_runtime_citation(self) -> None:
        report = runtime_report()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            try:
                verified = VERIFY_MODULE.verify(path)
            except ValueError as exc:
                self.fail(f"slide-level citation was rejected: {exc}")

        self.assertEqual(verified["websocket"]["citations"][0]["slide"], 1)

    def test_rejects_fixture_synthesized_report(self) -> None:
        report = {
            "traceId": "rha-e2e-trace",
            "pipeline": {
                "status": "SEARCHABLE",
                "documentVersion": "fixture-version",
                "alias": "rha-knowledge-active",
            },
            "answer": {
                "citations": [
                    {"evidenceId": "fixture-evidence", "page": 2}
                ]
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime"):
                VERIFY_MODULE.verify(path)

    def test_rejects_runtime_report_without_authenticated_api_call(self) -> None:
        report = {
            "reportKind": "rha-runtime-e2e",
            "traceId": "rha-e2e-trace",
            "pipeline": {
                "status": "SEARCHABLE",
                "documentVersion": "content-version",
                "alias": "rha-knowledge-active",
            },
            "answer": {
                "citations": [
                    {"evidenceId": "runtime-evidence", "page": 2}
                ]
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "auth.tokenAcquired"):
                VERIFY_MODULE.verify(path)

    def test_rejects_report_without_duplicate_chunk_and_merge_observations(self) -> None:
        report = {
            "reportKind": "rha-runtime-e2e",
            "auth": {"tokenAcquired": True},
            "traceId": "rha-e2e-trace",
            "pipeline": {
                "status": "SEARCHABLE",
                "documentVersion": "content-version",
                "alias": "rha-knowledge-active",
            },
            "answer": {
                "citations": [
                    {"evidenceId": "runtime-evidence", "page": 2}
                ]
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "upload.chunkRequests"):
                VERIFY_MODULE.verify(path)

    def test_rejects_pipeline_without_all_four_successful_stages(self) -> None:
        report = runtime_report()
        report["pipeline"]["stages"] = report["pipeline"]["stages"][:-1]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pipeline.stages"):
                VERIFY_MODULE.verify(path)

    def test_rejects_pipeline_without_elasticsearch_alias_readback(self) -> None:
        report = runtime_report()
        del report["pipeline"]["aliasReadback"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "aliasReadback"):
                VERIFY_MODULE.verify(path)

    def test_rejects_report_without_runtime_retrieval_hit(self) -> None:
        report = runtime_report()
        report["retrieval"]["hits"] = []

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "retrieval.hits"):
                VERIFY_MODULE.verify(path)

    def test_rejects_retrieval_and_citation_from_an_older_upload(self) -> None:
        report = runtime_report()
        report["retrieval"]["hits"][0]["fileMd5"] = "fedcba9876543210fedcba9876543210"
        report["retrieval"]["hits"][0]["citations"][0]["documentVersion"] = "old-version"
        report["websocket"]["citations"][0]["documentVersion"] = "old-version"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "uploaded file"):
                VERIFY_MODULE.verify(path)

    def test_rejects_websocket_citation_not_present_in_retrieval(self) -> None:
        report = runtime_report()
        report["websocket"]["citations"][0]["evidenceId"] = "invented-evidence"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "match a retrieval citation"):
                VERIFY_MODULE.verify(path)

    def test_rejects_trace_mismatch_across_retrieval_and_websocket(self) -> None:
        report = runtime_report()
        report["websocket"]["traceId"] = "different-trace"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "traceId"):
                VERIFY_MODULE.verify(path)

    def test_rejects_websocket_without_streamed_answer(self) -> None:
        report = runtime_report()
        report["websocket"]["answer"] = ""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "websocket.answer"):
                VERIFY_MODULE.verify(path)

    def test_rejects_schema_v3_report_without_recovery_object(self) -> None:
        report = recovery_report()
        del report["recovery"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recovery"):
                VERIFY_MODULE.verify(path)

    def test_rejects_schema_v4_report_without_recovery_object(self) -> None:
        report = reliability_report()
        del report["recovery"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recovery"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_when_dlq_id_does_not_match_envelope(self) -> None:
        report = recovery_report()
        report["recovery"]["dlq"]["messageId"] = "e" * 64

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DLQ"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_when_dlq_payload_id_does_not_match(self) -> None:
        report = recovery_report()
        report["recovery"]["dlq"]["payload"]["dlq_id"] = "e" * 64

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "payload.*dlq_id"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_when_dlq_payload_file_md5_does_not_match(self) -> None:
        report = recovery_report()
        report["recovery"]["dlq"]["payload"]["file_md5"] = report["upload"]["fileMd5"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "payload.*fileMd5"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_when_dlq_payload_document_version_does_not_match(self) -> None:
        report = recovery_report()
        report["recovery"]["dlq"]["payload"]["document_version"] = "old-version"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "payload.*documentVersion"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_without_all_four_successful_stages(self) -> None:
        report = recovery_report()
        report["recovery"]["pipeline"]["stages"] = report["recovery"]["pipeline"]["stages"][:-1]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recovery.*stages"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_when_replay_result_is_incorrect(self) -> None:
        report = recovery_report()
        report["recovery"]["replay"]["replayedTasks"] = 0

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replay"):
                VERIFY_MODULE.verify(path)

    def test_rejects_recovery_when_elasticsearch_counts_are_duplicated(self) -> None:
        report = recovery_report()
        report["recovery"]["elasticsearch"]["evidenceCount"] = 2

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                VERIFY_MODULE.verify(path)


if __name__ == "__main__":
    unittest.main()

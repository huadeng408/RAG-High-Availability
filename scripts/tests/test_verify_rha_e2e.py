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
            "merge": {"statusCode": 200, "traceId": "upload-trace"},
            "resumeCheck": {"source": "POST /api/v1/upload/check", "statusCode": 200, "completed": False, "uploadedChunks": [0]},
        },
        "pipeline": {
            "source": "GET /api/v1/documents/pipeline-status",
            "status": "SEARCHABLE",
            "documentVersion": "content-version",
            "alias": "rha-knowledge-active",
            "aliasReadback": {
                "source": "GET /_alias/rha-knowledge-active",
                "statusCode": 200,
                "indices": ["rha-knowledge-v2"],
            },
            "aliasMigration": {
                "previousIndex": "rha-knowledge-v2",
                "newIndex": "rha-knowledge-v3-probe",
                "mappingVerified": True,
                "readbackVerified": True,
                "switchedIndices": ["rha-knowledge-v3-probe"],
                "rollbackIndices": ["rha-knowledge-v2"],
            },
            "stages": [
                {"stage": "parse", "status": "SUCCESS", "attemptCount": 1, "lastTraceId": "upload-trace"},
                {"stage": "chunk", "status": "SUCCESS", "attemptCount": 1, "lastTraceId": "upload-trace"},
                {"stage": "embed", "status": "SUCCESS", "attemptCount": 1, "lastTraceId": "upload-trace"},
                {"stage": "index", "status": "SUCCESS", "attemptCount": 1, "lastTraceId": "upload-trace"},
            ],
        },
        "retrieval": {
            "source": "GET /api/v1/search/hybrid",
            "statusCode": 200,
            "traceId": "retrieval-trace",
            "hits": [{"fileMd5": "0123456789abcdef0123456789abcdef", "citations": [dict(citation)]}],
        },
        "websocket": {
            "source": "GET /chat/:token",
            "traceId": "websocket-trace",
            "answer": "The retention period is seven years.",
            "citations": [dict(citation)],
            "events": [
                {"type": "chunk", "chunk": "The retention period is seven years.", "traceId": "websocket-trace"},
                {"type": "completion", "status": "finished", "traceId": "websocket-trace"},
            ],
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
            "merge": {"statusCode": 200, "traceId": "image-upload-trace"},
        },
        "pipeline": {
            "status": "SEARCHABLE",
            "documentVersion": "image-version",
            "stages": [
                {"stage": stage, "status": "SUCCESS", "attemptCount": 1, "lastTraceId": "image-upload-trace"}
                for stage in ("parse", "chunk", "embed", "index")
            ],
        },
        "retrieval": {
            "statusCode": 200,
            "traceId": "image-retrieval-trace",
            "hits": [{"fileMd5": "fedcba9876543210fedcba9876543210", "citations": [dict(image_citation)]}],
        },
        "websocket": {
            "traceId": "image-websocket-trace",
            "answer": "The image inspection code is IMG-2048.",
            "citations": [dict(image_citation)],
            "events": [
                {"type": "chunk", "chunk": "The image inspection code is IMG-2048.", "traceId": "image-websocket-trace"},
                {"type": "completion", "status": "finished", "traceId": "image-websocket-trace"},
            ],
        },
    }
    report["multimodalEvidence"] = {
        "source": "POST /rha-evidence-active/_search",
        "modalities": ["excel", "image", "pdf", "ppt", "word"],
        "total": 57,
        "counts": {"pdf": 14, "word": 14, "ppt": 14, "excel": 14, "image": 1},
        "allVersioned": True,
        "allLocated": True,
        "allDurableAssets": True,
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
            "merge": {"statusCode": 200, "traceId": "recovery-upload-trace"},
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
                {"stage": stage, "status": "SUCCESS", "attemptCount": 2, "replayCount": 1, "lastTraceId": "recovery-upload-trace"}
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
    first_events = [
        {"type": "chunk", "chunk": "Remembered RHA-MEMORY-1", "traceId": "memory-turn-one"},
        {"type": "completion", "status": "finished", "traceId": "memory-turn-one", "citations": []},
    ]
    second_events = [
        {"type": "chunk", "chunk": "RHA-MEMORY-1", "traceId": "memory-turn-two"},
        {"type": "completion", "status": "finished", "traceId": "memory-turn-two", "citations": []},
    ]
    report["reliability"] = {
        "degradation": {
            "embeddingFailureFallback": True,
            "rerankerTimeoutFallback": True,
            "rerankerControl": {
                "requestedDelayMs": 500,
                "readbackDelayMs": 500,
                "configuredTimeoutMs": 200,
                "requestElapsedMs": 220,
                "returnedBeforeDelay": True,
            },
        },
        "permission": {
            "permittedHit": True,
            "foreignPrivateAbsent": True,
            "citationsFiltered": True,
            "answerFiltered": True,
            "foreignDocumentId": "foreign-private-1",
            "foreignMarker": "FOREIGN-PRIVATE-1",
            "retrieval": {"hits": []},
            "websocket": {"answer": "No permitted evidence.", "citations": []},
        },
        "memory": {
            "marker": "RHA-MEMORY-1",
            "firstTurnStored": True,
            "secondTurnRetrieved": True,
            "durable": True,
            "shortTermHistoryCleared": True,
            "mysqlMarkerCount": 1,
            "elasticsearchMarkerCount": 1,
            "redisKeysBefore": ["conversation:1"],
            "redisKeysAfter": [],
            "readbackItems": [{"text": "Durable marker RHA-MEMORY-1"}],
            "turns": [
                {"traceId": "memory-turn-one", "answer": "Remembered RHA-MEMORY-1", "events": first_events},
                {"traceId": "memory-turn-two", "answer": "RHA-MEMORY-1", "events": second_events},
            ],
        },
        "trace": {"events": first_events + second_events},
        "graph": {
            "nodes": list(VERIFY_MODULE.GRAPH_NODES),
            "edges": [[left, right] for left, right in zip(
                ["__start__", *VERIFY_MODULE.GRAPH_NODES, "__end__"],
                ["__start__", *VERIFY_MODULE.GRAPH_NODES, "__end__"][1:],
            )],
        },
        "brokerOutage": {
            "brokerStopped": True,
            "outboxPersisted": True,
            "automaticRecovery": True,
            "mergeRequestCount": 1,
            "upload": {
                "fileMd5": "0123456789abcdef0123456789abcdef",
                "merge": {"statusCode": 200, "traceId": "broker-trace"},
            },
            "publicationBeforeRecovery": {
                "status": "PENDING",
                "publicationAttemptCount": 1,
                "processingAttemptCount": 0,
                "published": False,
                "lastErrorPresent": True,
            },
            "publicationAfterRecovery": {
                "status": "PUBLISHED",
                "publicationAttemptCount": 2,
                "processingAttemptCount": 1,
                "published": True,
                "lastErrorPresent": False,
            },
            "pipeline": {
                "status": "SEARCHABLE",
                "documentVersion": "broker-version",
                "stages": [
                    {"stage": stage, "status": "SUCCESS", "attemptCount": 1}
                    for stage in ("parse", "chunk", "embed", "index")
                ],
            },
            "retrieval": {
                "hits": [{
                    "fileMd5": "0123456789abcdef0123456789abcdef",
                    "documentVersion": "broker-version",
                    "citations": [{"evidenceId": "broker-evidence"}],
                }],
            },
            "websocket": {
                "answer": "Recovered from durable outbox.",
                "citations": [{"evidenceId": "broker-evidence"}],
            },
            "elasticsearch": {
                "knowledgeCount": 1,
                "uniqueKnowledgeUnits": 1,
                "evidenceCount": 1,
                "uniqueEvidenceUnits": 1,
                "knowledgeIds": ["broker-knowledge"],
                "evidenceIds": ["broker-evidence"],
            },
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
        for dimension in ("degradation", "permission", "memory", "trace", "graph", "brokerOutage"):
            report = reliability_report()
            del report["reliability"][dimension]
            with self.subTest(dimension=dimension), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(ValueError):
                    VERIFY_MODULE.verify(path)

    def test_rejects_malformed_broker_outage_evidence(self) -> None:
        mutations = (
            lambda outage: outage.update(brokerStopped=False),
            lambda outage: outage.update(mergeRequestCount=2),
            lambda outage: outage["publicationBeforeRecovery"].update(processingAttemptCount=1),
            lambda outage: outage["publicationAfterRecovery"].update(publicationAttemptCount=1),
            lambda outage: outage["pipeline"].update(status="PROCESSING"),
            lambda outage: outage["pipeline"].update(documentVersion="upload:" + outage["upload"]["fileMd5"]),
            lambda outage: outage["retrieval"].update(hits=[]),
            lambda outage: outage["websocket"].update(answer=""),
            lambda outage: outage["websocket"].update(citations=[{"evidenceId": "other"}]),
            lambda outage: outage["elasticsearch"].update(knowledgeCount=0, uniqueKnowledgeUnits=0),
            lambda outage: outage["elasticsearch"].update(knowledgeIds=["duplicate", "duplicate"]),
            lambda outage: outage["elasticsearch"].pop("evidenceIds"),
            lambda outage: outage["elasticsearch"].update(evidenceIds=[]),
        )
        for mutate in mutations:
            report = reliability_report()
            mutate(report["reliability"]["brokerOutage"])
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "brokerOutage"):
                    VERIFY_MODULE.verify(path)

    def test_rejects_memory_without_marker_specific_durable_evidence(self) -> None:
        mutations = (
            lambda memory: memory.update(mysqlMarkerCount=0),
            lambda memory: memory.update(elasticsearchMarkerCount=0),
            lambda memory: memory.update(readbackItems=[{"text": "unrelated memory"}]),
        )
        for mutate in mutations:
            report = reliability_report()
            mutate(report["reliability"]["memory"])
            with self.subTest(report=report), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "marker"):
                    VERIFY_MODULE.verify(path)

    def test_rejects_memory_without_redis_absence_readback(self) -> None:
        report = reliability_report()
        report["reliability"]["memory"]["redisKeysAfter"] = ["conversation:1"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Redis"):
                VERIFY_MODULE.verify(path)

    def test_rejects_permission_evidence_from_unrelated_chat(self) -> None:
        report = reliability_report()
        report["reliability"]["permission"]["websocket"]["answer"] = "FOREIGN-PRIVATE-1"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "foreign"):
                VERIFY_MODULE.verify(path)

    def test_rejects_extra_raw_runtime_graph_node_or_edge(self) -> None:
        for field, extra in (("nodes", "extra_node"), ("edges", ["load_history", "extra_node"])):
            report = reliability_report()
            report["reliability"]["graph"][field].append(extra)
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "exact"):
                    VERIFY_MODULE.verify(path)

    def test_rejects_trace_flattening_that_omits_or_masks_raw_turn_events(self) -> None:
        mutations = (
            lambda reliability: reliability["trace"]["events"].pop(0),
            lambda reliability: reliability["memory"]["turns"][0]["events"][0].update(traceId="wrong"),
            lambda reliability: reliability["memory"]["turns"][1]["events"].pop(),
        )
        for mutate in mutations:
            report = reliability_report()
            mutate(report["reliability"])
            with self.subTest(report=report), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "trace|turn|completion"):
                    VERIFY_MODULE.verify(path)

    def test_rejects_reranker_timeout_without_delay_readback_and_early_return(self) -> None:
        mutations = (
            lambda control: control.update(readbackDelayMs=0),
            lambda control: control.update(returnedBeforeDelay=False),
            lambda control: control.update(requestElapsedMs=600),
        )
        for mutate in mutations:
            report = reliability_report()
            mutate(report["reliability"]["degradation"]["rerankerControl"])
            with self.subTest(report=report), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "reranker"):
                    VERIFY_MODULE.verify(path)
    def test_rejects_report_without_image_runtime_path(self) -> None:
        report = runtime_report()
        del report["image"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "image"):
                VERIFY_MODULE.verify(path)

    def test_rejects_runtime_acceptance_below_54_five_modality_evidence_units(self) -> None:
        report = runtime_report()
        report["multimodalEvidence"]["total"] = 53
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "54"):
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

    def test_rejects_upload_without_interrupted_resume_check(self) -> None:
        report = runtime_report()
        del report["upload"]["resumeCheck"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "resumeCheck"):
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

    def test_rejects_alias_migration_without_rollback_to_previous_index(self) -> None:
        report = runtime_report()
        report["pipeline"]["aliasMigration"]["rollbackIndices"] = ["rha-knowledge-v3-probe"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "aliasMigration"):
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

    def test_accepts_current_version_citation_sets_with_a_non_first_overlap(self) -> None:
        report = runtime_report()
        overlapping = dict(report["websocket"]["citations"][0])
        report["websocket"]["citations"].insert(0, {
            **overlapping,
            "evidenceId": "ppt-slide-ranked-differently",
            "slide": 2,
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            VERIFY_MODULE.verify(path)

    def test_accepts_distinct_public_request_traces_with_pipeline_continuity(self) -> None:
        report = runtime_report()
        merge_trace = "a" * 16
        report["upload"]["merge"]["traceId"] = merge_trace
        for stage in report["pipeline"]["stages"]:
            stage["lastTraceId"] = merge_trace
        report["retrieval"]["traceId"] = "b" * 16
        report["websocket"]["traceId"] = "c" * 16
        report["websocket"]["events"] = [
            {"type": "chunk", "chunk": "The retention period is ", "traceId": "c" * 16},
            {"type": "completion", "status": "finished", "traceId": "c" * 16},
        ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            VERIFY_MODULE.verify(path)

    def test_rejects_pipeline_or_stream_trace_discontinuity(self) -> None:
        mutations = (
            lambda report: report["pipeline"]["stages"][2].update(lastTraceId="wrong"),
            lambda report: report["websocket"]["events"][0].update(traceId="wrong"),
        )
        for mutate in mutations:
            report = runtime_report()
            mutate(report)
            with self.subTest(report=report), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "traceId"):
                    VERIFY_MODULE.verify(path)

    def test_rejects_recovery_pipeline_trace_discontinuity(self) -> None:
        report = recovery_report()
        report["recovery"]["pipeline"]["stages"][3]["lastTraceId"] = "wrong"
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

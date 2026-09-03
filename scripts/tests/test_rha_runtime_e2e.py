from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "rha_runtime_e2e.py"
sys.path.insert(0, str(ROOT / "ai-orchestrator"))


def load_runtime_module():
    if not SCRIPT_PATH.exists():
        raise AssertionError("scripts/rha_runtime_e2e.py is required")
    spec = importlib.util.spec_from_file_location("rha_runtime_e2e", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RuntimeE2ETest(unittest.TestCase):
    def test_alias_probe_failure_restores_previous_target(self) -> None:
        runtime = load_runtime_module()

        class AliasClient:
            def __init__(self) -> None:
                self.active = "rha-knowledge-v2"

            def request_json(self, method: str, path: str, payload=None):
                if method == "GET" and path.startswith("/_alias/"):
                    return SimpleNamespace(body={self.active: {"aliases": {"rha-knowledge-active": {}}}}, status_code=200)
                if method == "GET" and path.endswith("/_mapping"):
                    return SimpleNamespace(body={self.active: {"mappings": {"properties": {"text_content": {}, "vector": {}}}}}, status_code=200)
                if method == "POST" and path == "/_aliases":
                    self.active = payload["actions"][-1]["add"]["index"]
                    return SimpleNamespace(body={"acknowledged": True}, status_code=200)
                if method == "PUT" and "/_doc/alias-probe-" in path:
                    raise RuntimeError("injected readback write failure")
                return SimpleNamespace(body={"acknowledged": True}, status_code=200)

        client = AliasClient()
        with self.assertRaisesRegex(RuntimeError, "injected readback"):
            runtime.exercise_alias_migration(client, "rha-knowledge-active", "run-1")
        self.assertEqual("rha-knowledge-v2", client.active)

    def test_alias_switch_timeout_after_apply_restores_previous_target(self) -> None:
        runtime = load_runtime_module()

        class AmbiguousTimeoutAliasClient:
            def __init__(self) -> None:
                self.active = "rha-knowledge-v2"
                self.switch_attempts = 0

            def request_json(self, method: str, path: str, payload=None):
                if method == "GET" and path.startswith("/_alias/"):
                    return SimpleNamespace(body={self.active: {"aliases": {"rha-knowledge-active": {}}}}, status_code=200)
                if method == "GET" and path.endswith("/_mapping"):
                    return SimpleNamespace(body={self.active: {"mappings": {"properties": {"text_content": {}, "vector": {}}}}}, status_code=200)
                if method == "POST" and path == "/_aliases":
                    self.active = payload["actions"][-1]["add"]["index"]
                    self.switch_attempts += 1
                    if self.switch_attempts == 1:
                        raise TimeoutError("alias switch response timed out after apply")
                    return SimpleNamespace(body={"acknowledged": True}, status_code=200)
                return SimpleNamespace(body={"acknowledged": True}, status_code=200)

        client = AmbiguousTimeoutAliasClient()
        with self.assertRaisesRegex(TimeoutError, "timed out after apply"):
            runtime.exercise_alias_migration(client, "rha-knowledge-active", "run-timeout")
        self.assertEqual("rha-knowledge-v2", client.active)
        self.assertEqual(2, client.switch_attempts)

    def test_dlq_consumer_scans_past_stale_retained_record(self) -> None:
        runtime = load_runtime_module()
        stale = json.dumps({"dlq_id": "old"})
        current = json.dumps({"dlq_id": "wanted", "stage": "embed"})
        completed = subprocess.CompletedProcess([], 1, stdout=stale + "\n" + current + "\n", stderr="timeout")
        with patch.object(runtime.subprocess, "run", return_value=completed) as run:
            envelope = runtime.consume_dlq_envelope(
                container="kafka", bootstrap_server="kafka:29092", topic="file-dlq",
                message_id="wanted", timeout_seconds=1,
            )
        self.assertEqual(envelope["dlq_id"], "wanted")
        self.assertNotIn("--max-messages", run.call_args.args[0])
    def test_model_failure_control_requires_reranker_delay_readback(self) -> None:
        runtime = load_runtime_module()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"embeddings": False, "reranker": False, "reranker_delay_ms": 0}).encode()

        with patch.object(runtime, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(RuntimeError, "delay"):
                runtime.set_model_failures("http://stub", reranker_delay_ms=500, timeout_seconds=1)

    def test_redis_clear_requires_post_delete_absence_readback(self) -> None:
        runtime = load_runtime_module()
        responses = iter(
            [
                SimpleNamespace(returncode=0, stdout="conversation:1\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="1\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="conversation:1\n", stderr=""),
            ]
        )
        with patch.object(runtime.subprocess, "run", side_effect=lambda *_args, **_kwargs: next(responses)):
            result = runtime.clear_redis_conversation_history("container", "password")
        self.assertFalse(result["cleared"])
        self.assertEqual(result["keysAfter"], ["conversation:1"])

    def test_run_runtime_exercises_api_and_writes_secret_free_report(self) -> None:
        runtime = load_runtime_module()
        observed: dict[str, object] = {
            "paths": [],
            "chunkFields": [],
            "chunkMediaTypes": [],
            "merges": [],
            "mergeTraces": {},
            "searchQueries": [],
            "websocketQueries": [],
            "websocketClosed": 0,
        }
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

        class Handler(BaseHTTPRequestHandler):
            def _write_json(self, payload: dict, *, trace: str = "") -> None:
                response = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                if trace:
                    self.send_header("X-Trace-ID", trace)
                self.end_headers()
                self.wfile.write(response)

            def do_POST(self) -> None:
                observed["paths"].append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if self.path.endswith("/users/register"):
                    observed["register"] = json.loads(body)
                    self._write_json({"code": 200})
                    return
                if self.path.endswith("/users/login"):
                    observed["login"] = json.loads(body)
                    self._write_json({"code": 200, "data": {"token": "secret-jwt"}})
                    return
                if self.path.endswith("/upload/check"):
                    self._write_json({"completed": False, "uploadedChunks": [0]})
                    return
                if self.path.endswith("/upload/chunk"):
                    message = BytesParser(policy=policy.default).parsebytes(
                        (
                            f"Content-Type: {self.headers['Content-Type']}\r\n"
                            "MIME-Version: 1.0\r\n\r\n"
                        ).encode()
                        + body
                    )
                    file_part = next(part for part in message.iter_parts() if part.get_filename())
                    fields = {
                        part.get_param("name", header="content-disposition"): (
                            part.get_payload(decode=True)
                            if part.get_filename()
                            else part.get_content().strip()
                        )
                        for part in message.iter_parts()
                    }
                    observed["chunkFields"].append(fields)
                    observed["chunkMediaTypes"].append(file_part.get_content_type())
                    self._write_json({"code": 200, "data": {"uploaded": [0], "totalChunks": 1}})
                    return
                if self.path.endswith("/upload/merge"):
                    merge_payload = json.loads(body)
                    observed["merges"].append(merge_payload)
                    merge_trace = merge_payload["fileMd5"][:16]
                    observed["mergeTraces"][merge_payload["fileMd5"]] = merge_trace
                    self._write_json(
                        {"code": 200, "data": {"object_url": "uploads/runtime"}},
                        trace=merge_trace,
                    )
                    return
                if self.path == "/_aliases":
                    actions = json.loads(body)["actions"]
                    observed["activeAlias"] = actions[-1]["add"]["index"]
                    self._write_json({"acknowledged": True})
                    return
                if self.path == "/rha-evidence-active/_search":
                    version = json.loads(body)["query"]["term"]["document_version"]
                    modality = version.split("-", 1)[0]
                    if modality == "content":
                        modality = "ppt"
                    count = 1 if modality == "image" else 14
                    sources = []
                    for index in range(count):
                        source = {"modality": modality, "document_version": version, "source_asset": f"merged/md5/file.{modality}"}
                        if modality == "word": source["heading_path"] = ["Evidence"]
                        elif modality == "excel": source["sheet_name"] = "Evidence"
                        elif modality == "ppt": source["slide_number"] = index + 1
                        elif modality == "pdf": source["page_number"] = index + 1
                        else: source["bbox"] = {"x0": 1, "y0": 1, "x1": 2, "y1": 2}
                        sources.append({"_source": source})
                    self._write_json({"hits": {"hits": sources}})
                    return
                self.send_error(404)

            def do_PUT(self) -> None:
                observed["paths"].append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if "/_doc/alias-probe-" in self.path:
                    observed["aliasProbe"] = json.loads(body)
                self._write_json({"acknowledged": True})

            def do_GET(self) -> None:
                observed["paths"].append(self.path)
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                chunks = observed["chunkFields"]
                if parsed.path.endswith("/documents/pipeline-status"):
                    file_md5 = (query.get("fileMd5") or [""])[0]
                    matching_chunk = next(
                        (fields for fields in chunks if fields["fileMd5"] == file_md5),
                        None,
                    )
                    self.assert_request(matching_chunk is not None)
                    suffix = Path(matching_chunk["fileName"]).suffix.lower()
                    modality = {".png": "image", ".docx": "word", ".xlsx": "excel", ".pdf": "pdf"}.get(suffix, "content")
                    self._write_json(
                        {
                            "code": 200,
                            "data": {
                                "fileMd5": file_md5,
                                "documentVersion": modality + "-version",
                                "status": "SEARCHABLE",
                                "stages": [
                                    {
                                        "stage": stage,
                                        "status": "SUCCESS",
                                        "attemptCount": 1,
                                        "lastTraceId": observed["mergeTraces"][file_md5],
                                    }
                                    for stage in ("parse", "chunk", "embed", "index")
                                ],
                            },
                        }
                    )
                    return
                if parsed.path.endswith("/search/hybrid"):
                    observed["searchQueries"].append(query)
                    query_text = (query.get("query") or [""])[0]
                    is_image = "image inspection code" in query_text
                    matching_chunk = next(
                        fields
                        for fields in chunks
                        if fields["fileName"].endswith(".png") == is_image
                    )
                    self._write_json(
                        {
                            "code": 200,
                            "data": [
                                {
                                    "fileMd5": matching_chunk["fileMd5"],
                                    "fileName": matching_chunk["fileName"],
                                    "textContent": (
                                        "RHA image inspection code is IMG-2048."
                                        if is_image
                                        else "RHA retention period is seven years."
                                    ),
                                    "citations": [image_citation if is_image else citation],
                                }
                            ],
                        },
                        trace=self.headers.get("X-Trace-ID", ""),
                    )
                    return
                if parsed.path == "/_alias/rha-knowledge-active":
                    self._write_json(
                        {observed.get("activeAlias", "rha-knowledge-v2"): {"aliases": {"rha-knowledge-active": {}}}}
                    )
                    return
                if parsed.path == "/rha-knowledge-v2/_mapping":
                    self._write_json({"rha-knowledge-v2": {"mappings": {"properties": {"text_content": {"type": "text"}, "vector": {"type": "dense_vector"}}}}})
                    return
                if "/_doc/alias-probe-" in parsed.path:
                    self._write_json({"found": True})
                    return
                self.send_error(404)

            def assert_request(self, condition: bool) -> None:
                if not condition:
                    raise AssertionError(f"unexpected request {self.path}")

            def log_message(self, format: str, *args) -> None:
                del format, args

        class FakeSocket:
            def __init__(self) -> None:
                self.messages = None

            def settimeout(self, timeout: float) -> None:
                observed["websocketTimeout"] = timeout

            def send(self, value: str) -> None:
                observed["websocketQueries"].append(value)
                is_image = "image inspection code" in value
                self.messages = iter(
                    [
                        json.dumps(
                            {
                                "chunk": (
                                    "The image inspection code is "
                                    if is_image
                                    else "The retention period is "
                                )
                            }
                        ),
                        json.dumps({"chunk": "IMG-2048." if is_image else "seven years."}),
                        json.dumps(
                            {
                                "type": "completion",
                                "status": "finished",
                                "traceId": "runtime-trace",
                                "citations": [image_citation if is_image else citation],
                            }
                        ),
                    ]
                )

            def recv(self) -> str:
                if self.messages is None:
                    raise AssertionError("websocket query must be sent before receiving")
                return next(self.messages)

            def close(self) -> None:
                observed["websocketClosed"] += 1

        def connect_websocket(url: str, *, timeout: float, header: list[str]):
            observed["websocketURL"] = url
            observed["websocketHeaders"] = header
            observed["websocketConnectTimeout"] = timeout
            return FakeSocket()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output_path = Path(directory) / "runtime-report.json"
                args = SimpleNamespace(
                    base_url=f"http://127.0.0.1:{server.server_port}",
                    elasticsearch_url=f"http://127.0.0.1:{server.server_port}",
                    out=str(output_path),
                    pipeline_timeout=1.0,
                    poll_interval=0.0,
                    request_timeout=5.0,
                    websocket_timeout=5.0,
                )
                exit_code = runtime.run_runtime(
                    args,
                    trace_id="runtime-trace",
                    websocket_connect=connect_websocket,
                )
                report_text = output_path.read_text(encoding="utf-8")
                report = json.loads(report_text)
                integrity_exists = output_path.with_suffix(output_path.suffix + ".integrity.json").is_file()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(observed["chunkFields"]), 11)
        uploads_by_md5 = {}
        for fields in observed["chunkFields"]:
            uploads_by_md5.setdefault(fields["fileMd5"], []).append(fields)
        self.assertEqual(len(uploads_by_md5), 5)
        self.assertTrue(all(len(items) >= 2 for items in uploads_by_md5.values()))
        self.assertEqual(observed["merges"][0]["fileMd5"], observed["chunkFields"][0]["fileMd5"])
        self.assertEqual({item["fileMd5"] for item in observed["merges"]}, set(uploads_by_md5))
        self.assertEqual(
            [item["query"] for item in observed["searchQueries"]],
            [["RHA retention period"], ["RHA image inspection code"]],
        )
        self.assertIn("/chat/secret-jwt", observed["websocketURL"])
        self.assertIn("X-Trace-ID: runtime-trace", observed["websocketHeaders"])
        self.assertEqual(observed["websocketQueries"], ["RHA retention period", "RHA image inspection code"])
        self.assertEqual(observed["websocketClosed"], 2)
        self.assertEqual(report["schemaVersion"], 2)
        self.assertEqual(report["pipeline"]["status"], "SEARCHABLE")
        self.assertEqual(report["pipeline"]["aliasReadback"]["indices"], ["rha-knowledge-v2"])
        self.assertTrue(any(observed["aliasProbe"]["vector"]))
        self.assertEqual(report["multimodalEvidence"]["total"], 57)
        self.assertTrue(integrity_exists)
        self.assertEqual(report["retrieval"]["hits"][0]["citations"], [citation])
        self.assertEqual(report["websocket"]["citations"], [citation])
        self.assertEqual(report["image"]["pipeline"]["status"], "SEARCHABLE")
        self.assertEqual(report["image"]["retrieval"]["hits"][0]["citations"][0]["modality"], "image")
        self.assertEqual(report["image"]["websocket"]["citations"][0]["image"]["width"], 320)
        self.assertNotIn("secret-jwt", report_text)
        self.assertNotIn(observed["login"]["password"], report_text)

    def test_cli_help_documents_runtime_inputs(self) -> None:
        completed = __import__("subprocess").run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--base-url", completed.stdout)
        self.assertIn("--elasticsearch-url", completed.stdout)
        self.assertIn("--out", completed.stdout)
        self.assertIn("--pipeline-timeout", completed.stdout)

    def test_http_client_sends_json_auth_and_trace_headers(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "RuntimeHTTPClient"), "RuntimeHTTPClient is required")
        observed = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                observed["path"] = self.path
                observed["headers"] = dict(self.headers)
                observed["body"] = json.loads(self.rfile.read(length))
                response = json.dumps({"code": 200, "data": {"token": "access"}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = runtime.RuntimeHTTPClient(
                f"http://127.0.0.1:{server.server_port}", "trace-123", timeout_seconds=5
            )
            response = client.request_json(
                "POST", "/api/v1/users/login", {"username": "u", "password": "p"}, token="jwt"
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["data"]["token"], "access")
        self.assertEqual(observed["path"], "/api/v1/users/login")
        self.assertEqual(observed["headers"]["Authorization"], "Bearer jwt")
        self.assertEqual(observed["headers"]["X-Trace-Id"], "trace-123")
        self.assertEqual(observed["body"], {"username": "u", "password": "p"})

    def test_http_client_posts_multipart_bytes(self) -> None:
        runtime = load_runtime_module()
        observed = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                observed["contentType"] = self.headers["Content-Type"]
                observed["body"] = self.rfile.read(length)
                response = b'{"code":200}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format: str, *args) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = runtime.RuntimeHTTPClient(
                f"http://127.0.0.1:{server.server_port}", "trace-123", timeout_seconds=5
            )
            self.assertTrue(hasattr(client, "request_bytes"), "request_bytes is required")
            response = client.request_bytes(
                "POST",
                "/api/v1/upload/chunk",
                b"multipart-body",
                content_type="multipart/form-data; boundary=test",
                token="jwt",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed["contentType"], "multipart/form-data; boundary=test")
        self.assertEqual(observed["body"], b"multipart-body")

    def test_collect_websocket_answer_uses_chunks_and_completion(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "collect_websocket_answer"), "collect_websocket_answer is required")

        class FakeSocket:
            def __init__(self) -> None:
                self.sent = []
                self.messages = iter(
                    [
                        '{"chunk":"seven "}',
                        '{"chunk":"years"}',
                        '{"type":"completion","status":"finished","traceId":"trace-1","citations":[{"evidenceId":"ppt-slide-1","documentVersion":"version-1","slide":1}]}',
                    ]
                )

            def settimeout(self, timeout: float) -> None:
                self.timeout = timeout

            def send(self, value: str) -> None:
                self.sent.append(value)

            def recv(self) -> str:
                return next(self.messages)

        socket = FakeSocket()
        answer, completion, events = runtime.collect_websocket_answer(
            socket, "What is the retention period?", timeout_seconds=5
        )

        self.assertEqual(socket.sent, ["What is the retention period?"])
        self.assertEqual(answer, "seven years")
        self.assertEqual(completion["traceId"], "trace-1")
        self.assertEqual(completion["citations"][0]["slide"], 1)
        self.assertEqual(len(events), 3)

    def test_split_chunks_preserves_content_and_chunk_size(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "split_chunks"), "split_chunks is required")

        chunks = runtime.split_chunks(b"abcdefgh", 3)

        self.assertEqual(chunks, [b"abc", b"def", b"gh"])
        with self.assertRaisesRegex(ValueError, "chunk_size"):
            runtime.split_chunks(b"data", 0)

    def test_multipart_body_contains_upload_fields_and_exact_chunk(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "encode_multipart"), "encode_multipart is required")

        body, content_type = runtime.encode_multipart(
            {"fileMd5": "file-md5", "chunkIndex": "0"},
            "runtime.pptx",
            b"pptx-bytes",
        )
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            + body
        )
        parts = {
            part.get_param("name", header="content-disposition"): part
            for part in message.iter_parts()
        }

        self.assertEqual(parts["fileMd5"].get_content().strip(), "file-md5")
        self.assertEqual(parts["chunkIndex"].get_content().strip(), "0")
        self.assertEqual(parts["file"].get_filename(), "runtime.pptx")
        self.assertEqual(parts["file"].get_payload(decode=True), b"pptx-bytes")

    def test_wait_for_searchable_polls_durable_status(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "wait_for_searchable"), "wait_for_searchable is required")
        responses = iter(
            [
                {"status": "PENDING"},
                {"status": "PROCESSING"},
                {"status": "SEARCHABLE", "documentVersion": "version-1"},
            ]
        )

        status, polls = runtime.wait_for_searchable(
            lambda: next(responses), timeout_seconds=1, poll_interval=0
        )

        self.assertEqual(status["documentVersion"], "version-1")
        self.assertEqual(polls, 3)

    def test_wait_for_searchable_stops_on_failed_stage(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "wait_for_searchable"), "wait_for_searchable is required")

        with self.assertRaisesRegex(RuntimeError, "mineru unavailable"):
            runtime.wait_for_searchable(
                lambda: {
                    "status": "FAILED",
                    "stages": [{"stage": "parse", "lastError": "mineru unavailable"}],
                },
                timeout_seconds=1,
                poll_interval=0,
            )

    def test_wait_for_dead_letter_ignores_transient_failures_until_dlq_metadata_exists(self) -> None:
        runtime = load_runtime_module()
        message_id = "e" * 64
        responses = iter(
            [
                {
                    "status": "FAILED",
                    "stages": [{"stage": "embed", "status": "FAILED", "retryCount": 1}],
                },
                {
                    "status": "FAILED",
                    "stages": [{"stage": "embed", "status": "FAILED", "retryCount": 2}],
                },
                {
                    "status": "FAILED",
                    "stages": [
                        {
                            "stage": "embed",
                            "status": "FAILED",
                            "retryCount": 3,
                            "dlqMessageId": message_id,
                            "deadLetteredAt": "2026-09-03T03:30:00+08:00",
                        }
                    ],
                },
            ]
        )

        status, polls = runtime.wait_for_dead_letter(
            lambda: next(responses),
            stage="embed",
            max_retries=2,
            timeout_seconds=1,
            poll_interval=0,
        )

        self.assertEqual(polls, 3)
        self.assertEqual(status["stages"][0]["dlqMessageId"], message_id)

    def test_cli_exposes_recovery_controls(self) -> None:
        runtime = load_runtime_module()
        args = runtime.build_argument_parser().parse_args(["--out", "report.json", "--exercise-replay"])
        self.assertTrue(args.exercise_replay)
        self.assertEqual(args.model_stub_control_url, "http://127.0.0.1:8010")

    def test_cli_exposes_reliability_control(self) -> None:
        runtime = load_runtime_module()
        args = runtime.build_argument_parser().parse_args(["--out", "report.json", "--exercise-reliability"])
        self.assertTrue(args.exercise_reliability)
        self.assertEqual(args.kafka_container, "rha-e2e-kafka-1")
        self.assertEqual(args.kafka_bootstrap_server, "kafka:29092")
        self.assertIsNone(args.admin_password)

    def test_admin_credentials_require_cli_or_environment_password(self) -> None:
        runtime = load_runtime_module()
        original = os.environ.pop("RHA_E2E_ADMIN_PASSWORD", None)
        try:
            with self.assertRaisesRegex(RuntimeError, "admin password"):
                runtime.resolve_admin_credentials(SimpleNamespace(admin_username="admin", admin_password=None))
        finally:
            if original is not None:
                os.environ["RHA_E2E_ADMIN_PASSWORD"] = original

    def test_consume_dlq_envelope_selects_requested_message(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "consume_dlq_envelope"), "consume_dlq_envelope is required")
        original = runtime.subprocess.run
        try:
            runtime.subprocess.run = lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"dlq_id": "other"}) + "\n" + json.dumps({"dlq_id": "wanted", "stage": "embed"}) + "\n",
                stderr="",
            )
            envelope = runtime.consume_dlq_envelope(
                container="kafka-container",
                bootstrap_server="kafka:29092",
                topic="file-dlq",
                message_id="wanted",
                timeout_seconds=1,
            )
        finally:
            runtime.subprocess.run = original
        self.assertEqual(envelope["stage"], "embed")

    def test_consume_dlq_envelope_requests_bounded_message_count(self) -> None:
        runtime = load_runtime_module()
        observed = {}
        original = runtime.subprocess.run
        try:
            def fake_run(command, **kwargs):
                observed["command"] = command
                return SimpleNamespace(returncode=0, stdout=json.dumps({"dlq_id": "wanted"}) + "\n", stderr="")
            runtime.subprocess.run = fake_run
            runtime.consume_dlq_envelope(
                container="kafka-container", bootstrap_server="kafka:29092", topic="file-dlq",
                message_id="wanted", timeout_seconds=1,
            )
        finally:
            runtime.subprocess.run = original
        self.assertNotIn("--max-messages", observed["command"])
        self.assertIn("--timeout-ms", observed["command"])

    def test_generated_pptx_is_parsed_as_slide_evidence(self) -> None:
        try:
            runtime = load_runtime_module()
        except AssertionError as exc:
            self.fail(str(exc))

        from app.structured_ingestion import PptParser

        contents = runtime.build_minimal_pptx("RHA retention period is seven years.")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.pptx"
            path.write_bytes(contents)
            parsed = PptParser().parse(path, "version-1")

        self.assertEqual(parsed.modality, "ppt")
        self.assertEqual(len(parsed.evidenceUnits), 14)
        self.assertEqual(parsed.evidenceUnits[0].slide, 1)
        self.assertIn("seven years", parsed.evidenceUnits[0].text)

    def test_generated_png_has_real_dimensions_without_external_assets(self) -> None:
        runtime = load_runtime_module()

        contents = runtime.build_minimal_png(320, 120)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.png"
            path.write_bytes(contents)
            from PIL import Image

            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (320, 120))

    def test_png_marker_changes_upload_identity_for_repeated_runs(self) -> None:
        runtime = load_runtime_module()
        self.assertNotEqual(runtime.build_minimal_png(320, 120, marker="run-a"), runtime.build_minimal_png(320, 120, marker="run-b"))

    def test_recovery_fixture_identity_is_distinct_from_image_fixture(self) -> None:
        runtime = load_runtime_module()
        self.assertTrue(hasattr(runtime, "build_recovery_png"), "build_recovery_png is required")
        self.assertNotEqual(
            runtime.build_minimal_png(320, 120, marker="run-a"),
            runtime.build_recovery_png("run-a"),
        )


if __name__ == "__main__":
    unittest.main()

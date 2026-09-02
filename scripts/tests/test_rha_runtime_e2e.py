from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
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
    def test_run_runtime_exercises_api_and_writes_secret_free_report(self) -> None:
        runtime = load_runtime_module()
        observed: dict[str, object] = {"paths": [], "chunkFields": []}
        citation = {
            "evidenceId": "ppt-slide-1",
            "documentVersion": "content-version",
            "modality": "ppt",
            "slide": 1,
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
                if self.path.endswith("/upload/chunk"):
                    message = BytesParser(policy=policy.default).parsebytes(
                        (
                            f"Content-Type: {self.headers['Content-Type']}\r\n"
                            "MIME-Version: 1.0\r\n\r\n"
                        ).encode()
                        + body
                    )
                    fields = {
                        part.get_param("name", header="content-disposition"): (
                            part.get_payload(decode=True)
                            if part.get_filename()
                            else part.get_content().strip()
                        )
                        for part in message.iter_parts()
                    }
                    observed["chunkFields"].append(fields)
                    self._write_json({"code": 200, "data": {"uploaded": [0], "totalChunks": 1}})
                    return
                if self.path.endswith("/upload/merge"):
                    observed["merge"] = json.loads(body)
                    self._write_json({"code": 200, "data": {"object_url": "uploads/runtime.pptx"}})
                    return
                self.send_error(404)

            def do_GET(self) -> None:
                observed["paths"].append(self.path)
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                chunks = observed["chunkFields"]
                file_md5 = chunks[0]["fileMd5"] if chunks else "missing"
                if parsed.path.endswith("/documents/pipeline-status"):
                    self.assert_request(query.get("fileMd5") == [file_md5])
                    self._write_json(
                        {
                            "code": 200,
                            "data": {
                                "fileMd5": file_md5,
                                "documentVersion": "content-version",
                                "status": "SEARCHABLE",
                                "stages": [
                                    {"stage": stage, "status": "SUCCESS", "attemptCount": 1}
                                    for stage in ("parse", "chunk", "embed", "index")
                                ],
                            },
                        }
                    )
                    return
                if parsed.path.endswith("/search/hybrid"):
                    observed["searchQuery"] = query
                    self._write_json(
                        {
                            "code": 200,
                            "data": [
                                {
                                    "fileMd5": file_md5,
                                    "fileName": "rha-runtime.pptx",
                                    "textContent": "RHA retention period is seven years.",
                                    "citations": [citation],
                                }
                            ],
                        },
                        trace=self.headers.get("X-Trace-ID", ""),
                    )
                    return
                if parsed.path == "/_alias/rha-knowledge-active":
                    self._write_json(
                        {"rha-knowledge-v1": {"aliases": {"rha-knowledge-active": {}}}}
                    )
                    return
                self.send_error(404)

            def assert_request(self, condition: bool) -> None:
                if not condition:
                    raise AssertionError(f"unexpected request {self.path}")

            def log_message(self, format: str, *args) -> None:
                del format, args

        class FakeSocket:
            def __init__(self) -> None:
                self.messages = iter(
                    [
                        '{"chunk":"The retention period is "}',
                        '{"chunk":"seven years."}',
                        json.dumps(
                            {
                                "type": "completion",
                                "status": "finished",
                                "traceId": "runtime-trace",
                                "citations": [citation],
                            }
                        ),
                    ]
                )

            def settimeout(self, timeout: float) -> None:
                observed["websocketTimeout"] = timeout

            def send(self, value: str) -> None:
                observed["websocketQuery"] = value

            def recv(self) -> str:
                return next(self.messages)

            def close(self) -> None:
                observed["websocketClosed"] = True

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
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(observed["chunkFields"]), 2)
        self.assertEqual(observed["chunkFields"][0], observed["chunkFields"][1])
        self.assertEqual(observed["merge"]["fileMd5"], observed["chunkFields"][0]["fileMd5"])
        self.assertEqual(observed["searchQuery"]["query"], ["RHA retention period"])
        self.assertIn("/chat/secret-jwt", observed["websocketURL"])
        self.assertIn("X-Trace-ID: runtime-trace", observed["websocketHeaders"])
        self.assertTrue(observed["websocketClosed"])
        self.assertEqual(report["pipeline"]["status"], "SEARCHABLE")
        self.assertEqual(report["pipeline"]["aliasReadback"]["indices"], ["rha-knowledge-v1"])
        self.assertEqual(report["retrieval"]["hits"][0]["citations"], [citation])
        self.assertEqual(report["websocket"]["citations"], [citation])
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
        self.assertEqual(len(parsed.evidenceUnits), 1)
        self.assertEqual(parsed.evidenceUnits[0].slide, 1)
        self.assertIn("seven years", parsed.evidenceUnits[0].text)


if __name__ == "__main__":
    unittest.main()

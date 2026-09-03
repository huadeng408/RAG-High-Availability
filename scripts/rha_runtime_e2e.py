#!/usr/bin/env python3
"""Exercise the RHA runtime from upload through cited WebSocket chat."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import secrets
import subprocess
import struct
import time
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


def split_chunks(contents: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [contents[offset : offset + chunk_size] for offset in range(0, len(contents), chunk_size)]


@dataclass(slots=True)
class HTTPResponse:
    status_code: int
    body: dict[str, Any]
    headers: dict[str, str]


class RuntimeHTTPClient:
    def __init__(self, base_url: str, trace_id: str, *, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._trace_id = trace_id
        self._timeout_seconds = timeout_seconds

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "X-Trace-ID": self._trace_id}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if extra_headers:
            headers.update(extra_headers)
        request = Request(
            self._base_url + "/" + path.lstrip("/"),
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
                body = json.loads(raw) if raw else {}
                return HTTPResponse(response.status, body, dict(response.headers.items()))
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"{method.upper()} {path} returned HTTP {exc.code}: {detail}") from exc

    def request_bytes(
        self,
        method: str,
        path: str,
        data: bytes,
        *,
        content_type: str,
        token: str = "",
    ) -> HTTPResponse:
        headers = {
            "Accept": "application/json",
            "Content-Type": content_type,
            "X-Trace-ID": self._trace_id,
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            self._base_url + "/" + path.lstrip("/"),
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
                body = json.loads(raw) if raw else {}
                return HTTPResponse(response.status, body, dict(response.headers.items()))
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"{method.upper()} {path} returned HTTP {exc.code}: {detail}") from exc


def encode_multipart(
    fields: dict[str, str],
    file_name: str,
    file_bytes: bytes,
    media_type: str | None = None,
) -> tuple[bytes, str]:
    boundary = "----rha-e2e-" + secrets.token_hex(12)
    boundary_bytes = boundary.encode("ascii")
    body = io.BytesIO()
    for name, value in fields.items():
        body.write(b"--" + boundary_bytes + b"\r\n")
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.write(str(value).encode("utf-8"))
        body.write(b"\r\n")
    body.write(b"--" + boundary_bytes + b"\r\n")
    body.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode("utf-8")
    )
    resolved_media_type = media_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    body.write(f"Content-Type: {resolved_media_type}\r\n\r\n".encode("ascii"))
    body.write(file_bytes)
    body.write(b"\r\n--" + boundary_bytes + b"--\r\n")
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def wait_for_searchable(
    fetch_status: Callable[[], dict[str, Any]],
    *,
    timeout_seconds: float,
    poll_interval: float,
) -> tuple[dict[str, Any], int]:
    deadline = time.monotonic() + timeout_seconds
    polls = 0
    last_status: dict[str, Any] = {}
    while True:
        last_status = fetch_status()
        polls += 1
        state = str(last_status.get("status", ""))
        if state == "SEARCHABLE":
            return last_status, polls
        if state == "FAILED":
            errors = [
                str(stage.get("lastError", "")).strip()
                for stage in last_status.get("stages") or []
                if str(stage.get("lastError", "")).strip()
            ]
            detail = "; ".join(errors) or "pipeline failed"
            raise RuntimeError(detail)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"pipeline did not become SEARCHABLE; last status={state or 'UNKNOWN'}")
        time.sleep(max(0.0, poll_interval))


def wait_for_dead_letter(
    fetch_status: Callable[[], dict[str, Any]],
    *,
    stage: str,
    max_retries: int,
    timeout_seconds: float,
    poll_interval: float,
) -> tuple[dict[str, Any], int]:
    deadline = time.monotonic() + timeout_seconds
    polls = 0
    last_status: dict[str, Any] = {}
    while True:
        last_status = fetch_status()
        polls += 1
        stage_status = next(
            (item for item in last_status.get("stages") or [] if item.get("stage") == stage),
            {},
        )
        message_id = str(stage_status.get("dlqMessageId", ""))
        if (
            stage_status.get("status") == "FAILED"
            and int(stage_status.get("retryCount", 0)) > max_retries
            and len(message_id) == 64
            and bool(stage_status.get("deadLetteredAt"))
        ):
            return last_status, polls
        if last_status.get("status") == "SEARCHABLE":
            raise RuntimeError(f"pipeline became SEARCHABLE before {stage} entered the DLQ")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"pipeline did not enter DLQ for stage {stage}; last status={last_status.get('status') or 'UNKNOWN'}"
            )
        time.sleep(max(0.0, poll_interval))


def set_model_embedding_failure(control_url: str, enabled: bool, *, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps({"embeddings": enabled}).encode("utf-8")
    request = Request(
        control_url.rstrip("/") + "/control/failures",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read() or b"{}")
    if not isinstance(body, dict) or body.get("embeddings") is not enabled:
        raise RuntimeError("model stub did not confirm embedding failure state")
    return body


def set_model_failures(
    control_url: str,
    *,
    embeddings: bool = False,
    reranker: bool = False,
    reranker_delay_ms: int = 0,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = json.dumps({
        "embeddings": embeddings,
        "reranker": reranker,
        "reranker_delay_ms": max(0, min(int(reranker_delay_ms), 30000)),
    }).encode("utf-8")
    request = Request(
        control_url.rstrip("/") + "/control/failures",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read() or b"{}")
    if not isinstance(body, dict):
        raise RuntimeError("model stub returned an invalid failure control response")
    if body.get("embeddings") is not embeddings or body.get("reranker") is not reranker:
        raise RuntimeError("model stub did not confirm requested failure state")
    return body


def consume_dlq_envelope(
    *,
    container: str,
    bootstrap_server: str,
    topic: str,
    message_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    command = [
        "docker", "exec", container, "kafka-console-consumer",
        "--bootstrap-server", bootstrap_server,
        "--topic", topic, "--from-beginning",
        "--property", "print.value=true",
        "--max-messages", "1",
        "--timeout-ms", str(max(1000, int(timeout_seconds * 1000))),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode not in (0, 1):
        raise RuntimeError(f"Kafka DLQ consumer failed: {completed.stderr.strip()}")
    for line in completed.stdout.splitlines():
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = str(envelope.get("dlq_id") or envelope.get("dlqMessageId") or envelope.get("message_id") or "")
        if candidate == message_id:
            return envelope
    raise RuntimeError(f"Kafka topic {topic} did not contain DLQ message {message_id}")



def collect_websocket_answer(socket: Any, query: str, *, timeout_seconds: float) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    socket.settimeout(timeout_seconds)
    socket.send(query)
    chunks: list[str] = []
    events: list[dict[str, Any]] = []
    while True:
        raw = socket.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        event = json.loads(raw)
        events.append(event)
        if event.get("error"):
            raise RuntimeError(f"websocket stream failed: {event['error']}")
        if event.get("chunk"):
            chunks.append(str(event["chunk"]))
        if event.get("type") == "completion" and event.get("status") == "finished":
            return "".join(chunks), event, events


def build_minimal_pptx(text: str) -> bytes:
    """Build a one-slide PPTX fixture without parser-side test shortcuts."""
    slide_text = escape(text)
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        "ppt/presentation.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
</p:presentation>""",
        "ppt/_rels/presentation.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>""",
        "ppt/slides/slide1.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/><p:sp><p:nvSpPr/><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{slide_text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>""",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return buffer.getvalue()


def build_minimal_png(width: int, height: int, marker: str = "") -> bytes:
    """Build a valid RGB PNG using only the standard library."""
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")

    def png_chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes((240, 240, 240)) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunks = [
        png_chunk(b"IHDR", header),
        png_chunk(b"IDAT", zlib.compress(row * height, level=9)),
    ]
    if marker:
        chunks.append(png_chunk(b"tEXt", b"rha-marker=" + marker.encode("utf-8")))
    chunks.append(png_chunk(b"IEND", b""))
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"".join(chunks)
    )


def build_recovery_png(run_suffix: str) -> bytes:
    """Build a recovery fixture whose content identity cannot match the image fixture."""
    return build_minimal_png(320, 120, marker=run_suffix + "-recovery")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--elasticsearch-url", default="http://127.0.0.1:9200")
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--pipeline-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--websocket-timeout", type=float, default=120.0)
    parser.add_argument("--exercise-replay", action="store_true")
    parser.add_argument("--exercise-reliability", action="store_true")
    parser.add_argument("--model-stub-control-url", default="http://127.0.0.1:8010")
    parser.add_argument("--kafka-container", default="rha-e2e-kafka-1")
    parser.add_argument("--kafka-bootstrap-server", default="kafka:29092")
    parser.add_argument("--kafka-dlq-topic", default="file-dlq")
    parser.add_argument("--mysql-container", default="rha-e2e-mysql-1")
    parser.add_argument("--orchestrator-container", default="rha-e2e-orchestrator-1")
    parser.add_argument("--admin-username", default=None)
    parser.add_argument("--admin-password", default=None)
    return parser


def resolve_admin_credentials(args: argparse.Namespace) -> tuple[str, str]:
    username = str(getattr(args, "admin_username", None) or os.environ.get("RHA_E2E_ADMIN_USERNAME") or "admin").strip()
    password = str(getattr(args, "admin_password", None) or os.environ.get("RHA_E2E_ADMIN_PASSWORD") or "")
    if not username:
        raise RuntimeError("admin username is required via --admin-username or RHA_E2E_ADMIN_USERNAME")
    if not password:
        raise RuntimeError("admin password is required via --admin-password or RHA_E2E_ADMIN_PASSWORD")
    return username, password


def _response_data(response: HTTPResponse, operation: str) -> Any:
    if response.body.get("code") not in (None, 200):
        raise RuntimeError(f"{operation} failed: {response.body.get('message') or response.body}")
    if "data" not in response.body:
        raise RuntimeError(f"{operation} response is missing data")
    return response.body["data"]


def _response_header(response: HTTPResponse, name: str) -> str:
    expected = name.casefold()
    for key, value in response.headers.items():
        if key.casefold() == expected:
            return str(value).strip()
    return ""


def _websocket_url(base_url: str, token: str) -> str:
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    base_path = parts.path.rstrip("/")
    path = f"{base_path}/chat/{quote(token, safe='')}"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def _connect_websocket(url: str, *, timeout: float, header: list[str]) -> Any:
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is required to run the runtime E2E") from exc
    return websocket.create_connection(url, timeout=timeout, header=header)


def run_runtime(
    args: argparse.Namespace,
    *,
    trace_id: str = "",
    websocket_connect: Callable[..., Any] | None = None,
) -> int:
    trace_id = trace_id.strip() or "rha-e2e-" + secrets.token_hex(12)
    run_suffix = secrets.token_hex(6)
    username = "rha-e2e-" + run_suffix
    password = secrets.token_urlsafe(24)
    client = RuntimeHTTPClient(args.base_url, trace_id, timeout_seconds=args.request_timeout)

    register = client.request_json(
        "POST",
        "/api/v1/users/register",
        {"username": username, "password": password},
    )
    login = client.request_json(
        "POST",
        "/api/v1/users/login",
        {"username": username, "password": password},
    )
    login_data = _response_data(login, "login")
    if not isinstance(login_data, dict) or not str(login_data.get("token", "")).strip():
        raise RuntimeError("login response did not contain an access token")
    token = str(login_data["token"])

    def upload_and_wait(
        file_name: str,
        contents: bytes,
        media_type: str,
        *,
        wait_mode: str = "searchable",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        file_md5 = hashlib.md5(contents).hexdigest()
        chunks = split_chunks(contents, 5 * 1024 * 1024)
        chunk_requests: list[dict[str, int]] = []

        def upload_chunk(chunk_index: int) -> None:
            chunk = chunks[chunk_index]
            fields = {
                "fileMd5": file_md5,
                "fileName": file_name,
                "totalSize": str(len(contents)),
                "chunkIndex": str(chunk_index),
                "chunkMd5": hashlib.md5(chunk).hexdigest(),
                "orgTag": "",
                "isPublic": "false",
            }
            body, content_type = encode_multipart(fields, file_name, chunk, media_type)
            response = client.request_bytes(
                "POST",
                "/api/v1/upload/chunk",
                body,
                content_type=content_type,
                token=token,
            )
            chunk_requests.append({"chunkIndex": chunk_index, "statusCode": response.status_code})

        for index in range(len(chunks)):
            upload_chunk(index)
        upload_chunk(0)
        merge = client.request_json(
            "POST",
            "/api/v1/upload/merge",
            {"fileMd5": file_md5, "fileName": file_name},
            token=token,
        )

        def fetch_pipeline_status() -> dict[str, Any]:
            response = client.request_json(
                "GET",
                "/api/v1/documents/pipeline-status?" + urlencode({"fileMd5": file_md5}),
                token=token,
            )
            data = _response_data(response, "pipeline status")
            if not isinstance(data, dict):
                raise RuntimeError("pipeline status response data must be an object")
            return data

        if wait_mode == "dead_letter":
            pipeline_status, poll_count = wait_for_dead_letter(
                fetch_pipeline_status,
                stage="embed",
                max_retries=2,
                timeout_seconds=args.pipeline_timeout,
                poll_interval=args.poll_interval,
            )
        else:
            pipeline_status, poll_count = wait_for_searchable(
                fetch_pipeline_status,
                timeout_seconds=args.pipeline_timeout,
                poll_interval=args.poll_interval,
            )
        return (
            {
                "fileMd5": file_md5,
                "fileName": file_name,
                "chunkCount": len(chunks),
                "chunkRequests": chunk_requests,
                "merge": {"statusCode": merge.status_code},
            },
            {
                "source": "GET /api/v1/documents/pipeline-status",
                "status": pipeline_status.get("status"),
                "documentVersion": pipeline_status.get("documentVersion"),
                "pollCount": poll_count,
                "stages": pipeline_status.get("stages") or [],
            },
        )

    query = "RHA retention period"
    file_name = f"rha-runtime-{run_suffix}.pptx"
    upload, pipeline = upload_and_wait(
        file_name,
        build_minimal_pptx(f"RHA retention period is seven years. Runtime evidence marker: {run_suffix}."),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    image_query = "RHA image inspection code"
    image_upload, image_pipeline = upload_and_wait(
        f"rha-image-{run_suffix}.png",
        build_minimal_png(320, 120, marker=run_suffix),
        "image/png",
    )

    alias_name = "rha-knowledge-active"
    elasticsearch = RuntimeHTTPClient(
        args.elasticsearch_url,
        trace_id,
        timeout_seconds=args.request_timeout,
    )
    alias_response = elasticsearch.request_json(
        "GET",
        "/_alias/" + quote(alias_name, safe=""),
    )
    alias_indices = sorted(
        index_name
        for index_name, index_data in alias_response.body.items()
        if isinstance(index_data, dict)
        and alias_name in (index_data.get("aliases") or {})
    )
    if not alias_indices:
        raise RuntimeError(f"Elasticsearch alias {alias_name} has no active index")

    def retrieve_and_chat(document_query: str) -> tuple[dict[str, Any], dict[str, Any]]:
        search = client.request_json(
            "GET",
            "/api/v1/search/hybrid?" + urlencode({"query": document_query, "topK": 5}),
            token=token,
        )
        hits = _response_data(search, "hybrid search")
        if not isinstance(hits, list):
            raise RuntimeError("hybrid search response data must be a list")

        connector = websocket_connect or _connect_websocket
        socket = connector(
            _websocket_url(args.base_url, token),
            timeout=args.websocket_timeout,
            header=[f"X-Trace-ID: {trace_id}"],
        )
        try:
            answer, completion, events = collect_websocket_answer(
                socket,
                document_query,
                timeout_seconds=args.websocket_timeout,
            )
        finally:
            socket.close()
        return (
            {
                "source": "GET /api/v1/search/hybrid",
                "statusCode": search.status_code,
                "traceId": _response_header(search, "X-Trace-ID"),
                "hits": hits,
            },
            {
                "source": "GET /chat/:token",
                "traceId": completion.get("traceId"),
                "answer": answer,
                "citations": completion.get("citations") or [],
                "events": events,
            },
        )

    retrieval, websocket = retrieve_and_chat(query)
    image_retrieval, image_websocket = retrieve_and_chat(image_query)

    reliability: dict[str, Any] | None = None
    if bool(getattr(args, "exercise_reliability", False)):
        profile = client.request_json("GET", "/api/v1/users/me", token=token)
        profile_data = _response_data(profile, "profile")
        user_id = int((profile_data or {}).get("id", 0))
        if user_id <= 0:
            raise RuntimeError("profile did not return an authenticated user id")
        control_url = str(getattr(args, "model_stub_control_url", "http://127.0.0.1:8010"))
        try:
            set_model_failures(control_url, embeddings=True, timeout_seconds=args.request_timeout)
            degraded_retrieval, _ = retrieve_and_chat(query)
            embedding_fallback = any(hit.get("fileMd5") == upload["fileMd5"] for hit in degraded_retrieval.get("hits") or [])

            set_model_failures(control_url, reranker_delay_ms=500, timeout_seconds=args.request_timeout)
            rerank_retrieval, _ = retrieve_and_chat(query)
            reranker_fallback = any(hit.get("fileMd5") == upload["fileMd5"] for hit in rerank_retrieval.get("hits") or [])
        finally:
            set_model_failures(control_url, timeout_seconds=args.request_timeout)

        foreign_marker = "FOREIGN-PRIVATE-" + run_suffix
        foreign_id = "foreign-private-" + run_suffix
        elasticsearch.request_json(
            "POST",
            "/" + quote(alias_name, safe="") + "/_doc/" + quote(foreign_id, safe="") + "?refresh=true",
            {"file_md5": foreign_id, "chunk_id": 1, "text_content": foreign_marker, "user_id": user_id + 100000,
             "org_tag": "foreign-private", "is_public": False, "document_version": foreign_id,
             "modality": "text", "evidence_ids": [foreign_id]},
        )
        foreign_search = client.request_json(
            "GET", "/api/v1/search/hybrid?" + urlencode({"query": foreign_marker, "topK": 10}), token=token,
        )
        foreign_hits = _response_data(foreign_search, "foreign private search")
        permitted_hit = any(hit.get("fileMd5") == upload["fileMd5"] for hit in retrieval.get("hits") or [])
        foreign_private_absent = not any(hit.get("fileMd5") == foreign_id for hit in foreign_hits or [])
        marker = "RHA-MEMORY-" + run_suffix
        _, memory_first = retrieve_and_chat("Remember this durable project marker: " + marker)

        memory_deadline = time.monotonic() + args.pipeline_timeout
        mysql_count = 0
        es_memory_count = 0
        while time.monotonic() < memory_deadline:
            db_password = os.environ.get("RHA_E2E_PASSWORD", "")
            completed = subprocess.run(
                ["docker", "exec", "-e", "MYSQL_PWD=" + db_password, str(args.mysql_container),
                 "mysql", "-N", "-uroot", "RHA", "-e", f"SELECT COUNT(*) FROM long_term_memories WHERE user_id={user_id};"],
                check=False, capture_output=True, text=True,
            )
            if completed.returncode == 0 and completed.stdout.strip().isdigit():
                mysql_count = int(completed.stdout.strip())
            try:
                es_count_response = elasticsearch.request_json(
                    "POST", "/conversation_memory/_count",
                    {"query": {"term": {"user_id": user_id}}},
                )
                es_memory_count = int((es_count_response.body or {}).get("count", 0))
            except RuntimeError:
                es_memory_count = 0
            if mysql_count > 0 and es_memory_count > 0:
                break
            time.sleep(max(0.1, args.poll_interval))
        redis_password = os.environ.get("RHA_E2E_PASSWORD", "")
        redis_env = ["docker", "exec", "-e", "REDISCLI_AUTH=" + redis_password, "rha-e2e-redis-1", "redis-cli"]
        scan = subprocess.run(
            [*redis_env, "--scan", "--pattern", "conversation:*"],
            check=False, capture_output=True, text=True,
        )
        conversation_keys = [line.strip() for line in scan.stdout.splitlines() if line.strip()]
        for key in conversation_keys:
            subprocess.run([*redis_env, "DEL", key], check=True, capture_output=True, text=True)
        recall_query = "What durable project marker did I ask you to remember?"
        memory_readback_response = client.request_json(
            "POST", "/internal/orchestrator/memory-search",
            {"user": {"id": user_id, "username": username}, "query": recall_query, "history": [],
             "plan": {"retrievalMode": "hybrid", "skipRetrieval": False}},
            extra_headers={"X-Internal-Token": os.environ.get("RHA_E2E_INTERNAL_TOKEN", "")},
        )
        memory_readback = _response_data(memory_readback_response, "memory readback")
        _, memory_second = retrieve_and_chat(recall_query)
        graph_probe = subprocess.run(
            ["docker", "exec", str(args.orchestrator_container), "python", "-c",
             "import json,sys; sys.path.insert(0,'/app/ai-orchestrator'); from app.main import graph; g=graph.get_graph(); print(json.dumps({'nodes':sorted(set(g.nodes)-{'__start__','__end__'}),'edges':[[e.source,e.target] for e in g.edges]}))"],
            check=False, capture_output=True, text=True,
        )
        if graph_probe.returncode != 0 or not graph_probe.stdout.strip():
            raise RuntimeError("failed to inspect the runtime LangGraph graph: " + graph_probe.stderr.strip())
        graph_contract = json.loads(graph_probe.stdout.strip().splitlines()[-1])
        graph_nodes = graph_contract.get("nodes") or []
        expected_graph_nodes = [
            "load_history", "classify_intent", "rewrite_query", "prepare_prompt_context",
            "retrieve_knowledge", "retrieve_memory", "fuse_context", "rerank_context",
            "build_messages", "generate_answer", "persist_memory",
        ]
        expected_graph_order = ["__start__", *expected_graph_nodes, "__end__"]
        actual_edges = {tuple(edge) for edge in graph_contract.get("edges") or []}
        ordered_graph_edges = [
            [left, right]
            for left, right in zip(expected_graph_order, expected_graph_order[1:])
            if (left, right) in actual_edges
        ]
        reliability = {
            "degradation": {
                "embeddingFailureFallback": embedding_fallback,
                "rerankerTimeoutFallback": reranker_fallback,
                "embeddingFileMd5": upload["fileMd5"],
                "rerankerFileMd5": upload["fileMd5"],
            },
            "permission": {
                "permittedHit": permitted_hit,
                "foreignPrivateAbsent": foreign_private_absent,
                "citationsFiltered": all(citation.get("evidenceId") != foreign_id for citation in websocket.get("citations") or []),
                "foreignDocumentId": foreign_id,
            },
            "memory": {
                "marker": marker,
                "firstTurnStored": mysql_count > 0,
                "secondTurnRetrieved": marker in str(memory_second.get("answer", "")),
                "durable": mysql_count > 0 and es_memory_count > 0,
                "mysqlCount": mysql_count,
                "elasticsearchCount": es_memory_count,
                "shortTermHistoryCleared": bool(conversation_keys),
                "readbackItems": (memory_readback or {}).get("items") or [],
                "turns": [memory_first, memory_second],
            },
            "trace": {"events": (memory_first.get("events") or []) + (memory_second.get("events") or [])},
            "graph": {
                "nodes": [name for name in expected_graph_nodes if name in graph_nodes],
                "edges": ordered_graph_edges,
            },
        }

    recovery: dict[str, Any] | None = None
    if bool(getattr(args, "exercise_replay", False)):
        control_url = str(getattr(args, "model_stub_control_url", "http://127.0.0.1:8010"))
        set_model_embedding_failure(control_url, True, timeout_seconds=args.request_timeout)
        recovery_file = f"rha-recovery-{run_suffix}.png"
        recovery_contents = build_recovery_png(run_suffix)
        try:
            recovery_upload, failed_pipeline = upload_and_wait(
                recovery_file,
                recovery_contents,
                "image/png",
                wait_mode="dead_letter",
            )
        finally:
            set_model_embedding_failure(control_url, False, timeout_seconds=args.request_timeout)
        failed_stage = next(
            (item for item in failed_pipeline.get("stages") or [] if item.get("stage") == "embed"),
            {},
        )
        dlq_id = str(failed_stage.get("dlqMessageId", ""))
        if not dlq_id:
            raise RuntimeError("failed embed stage did not expose dlqMessageId")
        dlq_envelope = consume_dlq_envelope(
            container=str(getattr(args, "kafka_container", "rha-e2e-kafka-1")),
            bootstrap_server=str(getattr(args, "kafka_bootstrap_server", "kafka:29092")),
            topic=str(getattr(args, "kafka_dlq_topic", "file-dlq")),
            message_id=dlq_id,
            timeout_seconds=args.pipeline_timeout,
        )
        admin = RuntimeHTTPClient(args.base_url, trace_id, timeout_seconds=args.request_timeout)
        admin_username, admin_password = resolve_admin_credentials(args)
        dlq_payload = dlq_envelope.get("payload") if isinstance(dlq_envelope, dict) else {}
        if not isinstance(dlq_payload, dict):
            dlq_payload = dlq_envelope if isinstance(dlq_envelope, dict) else {}
        document_version = str(failed_pipeline.get("documentVersion") or dlq_payload.get("document_version") or dlq_payload.get("documentVersion") or "")
        window_id = str(dlq_payload.get("window_id") or dlq_payload.get("windowId") or "")
        if not document_version or not window_id:
            raise RuntimeError("DLQ envelope did not contain document version and window identity")
        admin_login = admin.request_json(
            "POST",
            "/api/v1/users/login",
            {
                "username": admin_username,
                "password": admin_password,
            },
        )
        admin_token_data = _response_data(admin_login, "admin login")
        admin_token = str((admin_token_data or {}).get("token", ""))
        if not admin_token:
            raise RuntimeError("seeded administrator login did not return a token")
        replay_response = admin.request_json(
            "POST",
            "/api/v1/admin/pipeline/replay",
            {
                "fileMd5": recovery_upload["fileMd5"],
                "documentVersion": document_version,
                "stage": "embed",
                "windowId": window_id,
                "dlqMessageId": dlq_id,
            },
            token=admin_token,
        )
        replay_data = _response_data(replay_response, "pipeline replay")
        if not isinstance(replay_data, dict):
            raise RuntimeError("pipeline replay response data must be an object")
        recovery_client = RuntimeHTTPClient(args.base_url, trace_id, timeout_seconds=args.request_timeout)

        def fetch_recovery_status() -> dict[str, Any]:
            response = recovery_client.request_json(
                "GET",
                "/api/v1/documents/pipeline-status?" + urlencode({"fileMd5": recovery_upload["fileMd5"]}),
                token=token,
            )
            data = _response_data(response, "recovery pipeline status")
            if not isinstance(data, dict):
                raise RuntimeError("recovery pipeline status response data must be an object")
            return data

        recovered_pipeline, recovery_poll_count = wait_for_searchable(
            fetch_recovery_status,
            timeout_seconds=args.pipeline_timeout,
            poll_interval=args.poll_interval,
        )
        recovery_retrieval, recovery_websocket = retrieve_and_chat(image_query)
        recovery_alias = "rha-knowledge-active"

        def count_documents(alias: str, file_md5: str) -> int:
            response = elasticsearch.request_json(
                "POST",
                "/" + quote(alias, safe="") + "/_count",
                {"query": {"term": {"file_md5": file_md5}}},
            )
            return int((response.body or {}).get("count", 0))

        recovery = {
            "upload": recovery_upload,
            "stage": "embed",
            "dlqMessageId": dlq_id,
            "dlq": {
                "topic": str(getattr(args, "kafka_dlq_topic", "file-dlq")),
                "messageId": dlq_id,
                "payload": dlq_envelope,
            },
            "replay": {
                "statusCode": replay_response.status_code,
                "replayedTasks": int(replay_data.get("replayedTasks", 0)),
                "messageIds": replay_data.get("messageIds") or [],
            },
            "pipeline": {
                "source": "GET /api/v1/documents/pipeline-status",
                "status": recovered_pipeline.get("status"),
                "documentVersion": recovered_pipeline.get("documentVersion"),
                "pollCount": recovery_poll_count,
                "stages": recovered_pipeline.get("stages") or [],
            },
            "retrieval": recovery_retrieval,
            "websocket": recovery_websocket,
            "elasticsearch": {
                "knowledgeCount": count_documents(recovery_alias, recovery_upload["fileMd5"]),
                "evidenceCount": count_documents("rha-evidence-active", recovery_upload["fileMd5"]),
            },
        }

    report = {
        "reportKind": "rha-runtime-e2e",
        "schemaVersion": 4 if reliability is not None else (3 if recovery is not None else 2),
        "traceId": trace_id,
        "auth": {
            "registerStatusCode": register.status_code,
            "loginStatusCode": login.status_code,
            "tokenAcquired": True,
        },
        "upload": upload,
        "pipeline": {
            **pipeline,
            "alias": alias_name,
            "aliasReadback": {
                "source": f"GET /_alias/{alias_name}",
                "statusCode": alias_response.status_code,
                "indices": alias_indices,
            },
        },
        "retrieval": retrieval,
        "websocket": websocket,
        "image": {
            "upload": image_upload,
            "pipeline": image_pipeline,
            "retrieval": image_retrieval,
            "websocket": image_websocket,
        },
    }
    if recovery is not None:
        report["recovery"] = recovery
    if reliability is not None:
        report["reliability"] = reliability
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    args = build_argument_parser().parse_args()
    return run_runtime(args)


if __name__ == "__main__":
    raise SystemExit(main())

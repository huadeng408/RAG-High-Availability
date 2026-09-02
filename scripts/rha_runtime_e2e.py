#!/usr/bin/env python3
"""Exercise the RHA runtime from upload through cited WebSocket chat."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import secrets
import time
import zipfile
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
    ) -> HTTPResponse:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "X-Trace-ID": self._trace_id}
        if data is not None:
            headers["Content-Type"] = "application/json"
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


def encode_multipart(fields: dict[str, str], file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
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
    body.write(b"Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation\r\n\r\n")
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


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--elasticsearch-url", default="http://127.0.0.1:9200")
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--pipeline-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--websocket-timeout", type=float, default=120.0)
    return parser


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

    query = "RHA retention period"
    file_name = f"rha-runtime-{run_suffix}.pptx"
    contents = build_minimal_pptx(
        f"RHA retention period is seven years. Runtime evidence marker: {run_suffix}."
    )
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
        body, content_type = encode_multipart(fields, file_name, chunk)
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

    pipeline_status, poll_count = wait_for_searchable(
        fetch_pipeline_status,
        timeout_seconds=args.pipeline_timeout,
        poll_interval=args.poll_interval,
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

    search = client.request_json(
        "GET",
        "/api/v1/search/hybrid?" + urlencode({"query": query, "topK": 5}),
        token=token,
    )
    hits = _response_data(search, "hybrid search")
    if not isinstance(hits, list):
        raise RuntimeError("hybrid search response data must be a list")
    retrieval_trace_id = _response_header(search, "X-Trace-ID")

    connector = websocket_connect or _connect_websocket
    socket = connector(
        _websocket_url(args.base_url, token),
        timeout=args.websocket_timeout,
        header=[f"X-Trace-ID: {trace_id}"],
    )
    try:
        answer, completion, _events = collect_websocket_answer(
            socket,
            query,
            timeout_seconds=args.websocket_timeout,
        )
    finally:
        socket.close()

    report = {
        "reportKind": "rha-runtime-e2e",
        "schemaVersion": 1,
        "traceId": trace_id,
        "auth": {
            "registerStatusCode": register.status_code,
            "loginStatusCode": login.status_code,
            "tokenAcquired": True,
        },
        "upload": {
            "fileMd5": file_md5,
            "fileName": file_name,
            "chunkCount": len(chunks),
            "chunkRequests": chunk_requests,
            "merge": {"statusCode": merge.status_code},
        },
        "pipeline": {
            "source": "GET /api/v1/documents/pipeline-status",
            "status": pipeline_status.get("status"),
            "documentVersion": pipeline_status.get("documentVersion"),
            "alias": alias_name,
            "aliasReadback": {
                "source": f"GET /_alias/{alias_name}",
                "statusCode": alias_response.status_code,
                "indices": alias_indices,
            },
            "pollCount": poll_count,
            "stages": pipeline_status.get("stages") or [],
        },
        "retrieval": {
            "source": "GET /api/v1/search/hybrid",
            "statusCode": search.status_code,
            "traceId": retrieval_trace_id,
            "hits": hits,
        },
        "websocket": {
            "source": "GET /chat/:token",
            "traceId": completion.get("traceId"),
            "answer": answer,
            "citations": completion.get("citations") or [],
        },
    }
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    args = build_argument_parser().parse_args()
    return run_runtime(args)


if __name__ == "__main__":
    raise SystemExit(main())

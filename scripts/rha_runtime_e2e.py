#!/usr/bin/env python3
"""Exercise the RHA runtime from upload through cited WebSocket chat."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import subprocess
import struct
import time
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _docker_run(command: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    display_command = [
        argument.split("=", 1)[0] + "=<redacted>"
        if argument.startswith(("MYSQL_PWD=", "REDISCLI_AUTH="))
        else argument
        for argument in command
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"command {' '.join(display_command)} timed out after {timeout_seconds:g} seconds"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"command failed ({' '.join(display_command)}): {detail or 'no output'}")
    return completed


def set_kafka_broker_running(
    container: str,
    running: bool,
    *,
    bootstrap_server: str,
    timeout_seconds: float,
    poll_interval: float,
) -> dict[str, bool]:
    container = container.strip()
    if not container:
        raise RuntimeError("Kafka container name is required")
    name = _docker_run(["docker", "inspect", "--format", "{{.Name}}", container], timeout_seconds=timeout_seconds)
    if name.stdout.strip().lstrip("/") != container:
        raise RuntimeError(f"Kafka container identity mismatch: expected {container!r}, got {name.stdout.strip()!r}")

    desired = "true" if running else "false"
    deadline = time.monotonic() + timeout_seconds
    action = "start" if running else "stop"
    _docker_run(["docker", action, container], timeout_seconds=timeout_seconds)
    while True:
        state = _docker_run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            timeout_seconds=timeout_seconds,
        ).stdout.strip().lower()
        if state == desired:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Kafka broker did not reach running={desired}; last state={state!r}")
        time.sleep(max(0.0, poll_interval))

    if not running:
        return {"running": False, "ready": False}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Kafka broker did not become ready: readiness deadline expired")
        try:
            readiness = subprocess.run(
                ["docker", "exec", container, "kafka-topics", "--bootstrap-server", bootstrap_server, "--list"],
                check=False,
                capture_output=True,
                text=True,
                timeout=max(0.1, min(timeout_seconds, remaining)),
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("Kafka broker readiness command timed out") from exc
        if readiness.returncode == 0:
            return {"running": True, "ready": True}
        if time.monotonic() >= deadline:
            detail = (readiness.stderr or readiness.stdout).strip()
            raise TimeoutError(f"Kafka broker did not become ready: {detail or 'topic listing failed'}")
        time.sleep(max(0.0, poll_interval))


@contextmanager
def kafka_broker_outage(
    container: str,
    *,
    bootstrap_server: str,
    timeout_seconds: float,
    poll_interval: float,
) -> Iterator[dict[str, Any]]:
    try:
        stopped = set_kafka_broker_running(
            container,
            False,
            bootstrap_server=bootstrap_server,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    except BaseException as stop_error:
        try:
            set_kafka_broker_running(
                container,
                True,
                bootstrap_server=bootstrap_server,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
        except Exception as recovery_error:
            stop_error.add_note(f"Kafka recovery also failed: {recovery_error}")
        raise
    state: dict[str, Any] = {"stopped": stopped}
    primary_error: BaseException | None = None
    try:
        yield state
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            state["started"] = set_kafka_broker_running(
                container,
                True,
                bootstrap_server=bootstrap_server,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
        except Exception as recovery_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"Kafka recovery also failed: {recovery_error}")


def _parse_outbox_state(row: list[str]) -> dict[str, Any]:
    if len(row) != 5:
        raise ValueError(f"expected five outbox columns, got {len(row)}")
    try:
        publication_attempt_count = int(row[1])
        processing_attempt_count = int(row[2])
    except ValueError as exc:
        raise ValueError("outbox attempt counts must be integers") from exc
    return {
        "status": row[0].strip(),
        "publicationAttemptCount": publication_attempt_count,
        "processingAttemptCount": processing_attempt_count,
        "published": row[3].strip().upper() not in {"", "NULL"},
        "lastErrorPresent": bool(row[4].strip()),
    }


def parse_mysql_tsv_row(output: str) -> list[str]:
    row = output.rstrip("\r\n")
    if not row:
        return []
    if "\r" in row or "\n" in row:
        raise ValueError("expected exactly one MySQL outbox row")
    return row.split("\t")


def before_recovery_outbox_poll_interval(configured_interval: float) -> float:
    return max(0.01, min(float(configured_interval), 0.1))


def wait_for_outbox_state(
    fetch_row: Callable[[], list[str]],
    *,
    phase: str,
    timeout_seconds: float,
    poll_interval: float,
    previous_publication_attempt_count: int = 0,
) -> tuple[dict[str, Any], int]:
    if phase not in {"before-recovery", "after-recovery"}:
        raise ValueError(f"unsupported outbox phase: {phase}")
    poll_interval = max(0.01, poll_interval)
    deadline = time.monotonic() + timeout_seconds
    polls = 0
    last_observation: object = None
    while True:
        row = fetch_row()
        polls += 1
        last_observation = row
        try:
            state = _parse_outbox_state(row)
        except ValueError:
            state = None
        if state is not None:
            if phase == "before-recovery":
                matched = (
                    state["status"] == "PENDING"
                    and state["publicationAttemptCount"] >= 1
                    and state["processingAttemptCount"] == 0
                    and state["published"] is False
                    and state["lastErrorPresent"] is True
                )
            else:
                matched = (
                    state["status"] == "PUBLISHED"
                    and state["publicationAttemptCount"] > previous_publication_attempt_count
                    and state["processingAttemptCount"] >= 1
                    and state["published"] is True
                    and state["lastErrorPresent"] is False
                )
            if matched:
                return state, polls
            last_observation = state
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"broker outage {phase} outbox state was not observed; last observation={last_observation!r}"
            )
        time.sleep(max(0.0, poll_interval))


def _parse_memory_index_state(row: list[str]) -> dict[str, Any]:
    if len(row) != 6:
        raise ValueError(f"expected six memory index columns, got {len(row)}")
    try:
        attempt_count = int(row[1])
        flags = [int(value) for value in row[2:]]
    except ValueError as exc:
        raise ValueError("memory index attempt count and flags must be integers") from exc
    if attempt_count < 0 or any(flag not in (0, 1) for flag in flags):
        raise ValueError("memory index attempt count or flags are invalid")
    return {
        "status": row[0].strip(),
        "attemptCount": attempt_count,
        "claimed": flags[0] == 1,
        "retryScheduled": flags[1] == 1,
        "lastErrorPresent": flags[2] == 1,
        "indexed": flags[3] == 1,
    }


def wait_for_memory_index_state(
    fetch_row: Callable[[], list[str]],
    *,
    phase: str,
    timeout_seconds: float,
    poll_interval: float,
    previous_attempt_count: int = 0,
) -> tuple[dict[str, Any], int]:
    if phase not in {"before-recovery", "after-recovery"}:
        raise ValueError(f"unsupported memory index phase: {phase}")
    deadline = time.monotonic() + timeout_seconds
    polls = 0
    last_observation: object = None
    while True:
        row = fetch_row()
        polls += 1
        last_observation = row
        try:
            state = _parse_memory_index_state(row)
        except ValueError:
            state = None
        if state is not None:
            if phase == "before-recovery":
                matched = (
                    state["status"] == "PENDING"
                    and state["attemptCount"] >= 1
                    and state["claimed"] is False
                    and state["retryScheduled"] is True
                    and state["lastErrorPresent"] is True
                    and state["indexed"] is False
                )
            else:
                matched = (
                    state["status"] == "INDEXED"
                    and state["attemptCount"] > previous_attempt_count
                    and state["claimed"] is False
                    and state["retryScheduled"] is False
                    and state["lastErrorPresent"] is False
                    and state["indexed"] is True
                )
            if matched:
                return state, polls
            last_observation = state
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"memory indexing {phase} state was not observed; last observation={last_observation!r}"
            )
        time.sleep(max(0.0, poll_interval))


def read_memory_index_mapping(
    elasticsearch: RuntimeHTTPClient,
    index_name: str,
    expected_dimensions: int = 8,
) -> dict[str, Any]:
    resolved_name = index_name.strip()
    if not resolved_name:
        raise RuntimeError("memory index name is required")
    response = elasticsearch.request_json("GET", f"/{quote(resolved_name, safe='')}/_mapping")
    body = response.body if isinstance(response.body, dict) else {}
    index_mapping = body.get(resolved_name) if isinstance(body, dict) else None
    properties = ((index_mapping or {}).get("mappings") or {}).get("properties") or {}
    vector_mapping = properties.get("vector") or {}
    vector_type = str(vector_mapping.get("type", "")).strip()
    if vector_type != "dense_vector":
        raise RuntimeError(
            f"memory index {resolved_name!r} vector mapping must be dense_vector, got {vector_type or 'missing'}"
        )
    dimensions = vector_mapping.get("dims")
    if type(dimensions) is not int or dimensions != expected_dimensions:
        raise RuntimeError(
            f"memory index {resolved_name!r} dimensions must be {expected_dimensions}, got {dimensions!r}"
        )
    return {"index": resolved_name, "vectorType": vector_type, "dimensions": dimensions}


def read_elasticsearch_uniqueness(
    elasticsearch: RuntimeHTTPClient,
    file_md5: str,
) -> dict[str, Any]:
    dimensions = (
        ("knowledge", "rha-knowledge-active", "vector_id", "knowledgeCount", "uniqueKnowledgeUnits", "knowledgeIds"),
        ("evidence", "rha-evidence-active", "evidence_id", "evidenceCount", "uniqueEvidenceUnits", "evidenceIds"),
    )
    summary: dict[str, Any] = {}
    for label, alias, source_field, count_field, unique_field, ids_field in dimensions:
        response = elasticsearch.request_json(
            "POST",
            f"/{alias}/_search",
            {
                "size": 10000,
                "_source": [source_field],
                "query": {"term": {"file_md5": file_md5}},
            },
        )
        hits = ((response.body or {}).get("hits") or {}).get("hits")
        if not isinstance(hits, list):
            raise RuntimeError(f"invalid Elasticsearch {label} search response")
        identities: list[str] = []
        for hit in hits:
            source = hit.get("_source") if isinstance(hit, dict) else None
            identity = str((source or {}).get(source_field) or (hit or {}).get("_id") or "").strip()
            if not identity:
                raise RuntimeError(f"Elasticsearch {label} hit is missing a persisted identity")
            identities.append(identity)
        unique_count = len(set(identities))
        if not identities:
            raise RuntimeError(f"Elasticsearch {label} count is zero after broker recovery")
        if unique_count != len(identities):
            raise RuntimeError(f"Elasticsearch contains duplicate {label} identities after broker recovery")
        summary.update({
            count_field: len(identities),
            unique_field: unique_count,
            ids_field: identities,
        })
    return summary


def set_model_failures(
    control_url: str,
    *,
    embeddings: bool = False,
    reranker: bool = False,
    reranker_delay_ms: int = 0,
    timeout_seconds: float,
) -> dict[str, Any]:
    requested_delay_ms = max(0, min(int(reranker_delay_ms), 30000))
    payload = json.dumps({
        "embeddings": embeddings,
        "reranker": reranker,
        "reranker_delay_ms": requested_delay_ms,
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
    if body.get("reranker_delay_ms") != requested_delay_ms:
        raise RuntimeError("model stub did not confirm requested reranker delay")
    return body


def clear_redis_conversation_history(container: str, password: str) -> dict[str, Any]:
    command = ["docker", "exec", "-e", "REDISCLI_AUTH=" + password, container, "redis-cli"]

    def scan_keys() -> list[str]:
        completed = subprocess.run(
            [*command, "--scan", "--pattern", "conversation:*"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError("Redis conversation scan failed: " + completed.stderr.strip())
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    keys_before = scan_keys()
    deleted_count = 0
    if keys_before:
        completed = subprocess.run(
            [*command, "DEL", *keys_before],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not completed.stdout.strip().isdigit():
            raise RuntimeError("Redis conversation deletion failed: " + completed.stderr.strip())
        deleted_count = int(completed.stdout.strip())
    keys_after = scan_keys()
    return {
        "keysBefore": keys_before,
        "keysAfter": keys_after,
        "deletedCount": deleted_count,
        "cleared": bool(keys_before) and not keys_after,
    }


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


def build_minimal_pptx(text: str, *, resume_padding_bytes: int = 0) -> bytes:
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
    slide_template = files.pop("ppt/slides/slide1.xml")
    for slide in range(1, 15):
        files[f"ppt/slides/slide{slide}.xml"] = slide_template.replace(slide_text, escape(f"{text} Evidence slide {slide}."))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
        if resume_padding_bytes:
            archive.writestr("ppt/media/resume-padding.bin", os.urandom(resume_padding_bytes), compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def build_minimal_docx(marker: str) -> bytes:
    heading = f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{escape(marker)} Evidence</w:t></w:r></w:p>'
    paragraphs = heading + "".join(f"<w:p><w:r><w:t>{escape(marker)} Word evidence {index}</w:t></w:r></w:p>" for index in range(1, 15))
    document = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{paragraphs}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def build_minimal_xlsx(marker: str) -> bytes:
    rows = ['<row r="1"><c t="inlineStr"><is><t>marker</t></is></c><c t="inlineStr"><is><t>value</t></is></c></row>']
    for index in range(1, 351):
        rows.append(f'<row r="{index + 1}"><c t="inlineStr"><is><t>{escape(marker)}</t></is></c><c><v>{index}</v></c></row>')
    workbook = '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Evidence" sheetId="1" r:id="rId1"/></sheets></workbook>'
    relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'
    worksheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(rows) + '</sheetData></worksheet>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
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
    parser.add_argument("--exercise-broker-outage", action="store_true")
    parser.add_argument("--model-stub-control-url", default="http://127.0.0.1:8010")
    parser.add_argument("--kafka-container", default="rha-e2e-kafka-1")
    parser.add_argument("--kafka-bootstrap-server", default="kafka:29092")
    parser.add_argument("--kafka-dlq-topic", default="file-dlq")
    parser.add_argument("--mysql-container", default="rha-e2e-mysql-1")
    parser.add_argument("--redis-container", default="rha-e2e-redis-1")
    parser.add_argument("--orchestrator-container", default="rha-e2e-orchestrator-1")
    parser.add_argument("--admin-username", default=None)
    parser.add_argument("--admin-password", default=None)
    return parser


def validate_runtime_args(args: argparse.Namespace) -> None:
    reliability = bool(getattr(args, "exercise_reliability", False))
    broker_outage = bool(getattr(args, "exercise_broker_outage", False))
    if reliability != broker_outage:
        raise RuntimeError(
            "--exercise-reliability and --exercise-broker-outage must be used together"
        )


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


def exercise_alias_migration(
    elasticsearch: RuntimeHTTPClient,
    alias_name: str,
    run_suffix: str,
) -> tuple[HTTPResponse, list[str], dict[str, Any]]:
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
    if len(alias_indices) != 1:
        raise RuntimeError(f"Elasticsearch alias {alias_name} must have exactly one active index")

    previous_index = alias_indices[0]
    mapping_response = elasticsearch.request_json("GET", "/" + quote(previous_index, safe="") + "/_mapping")
    previous_mapping = ((mapping_response.body.get(previous_index) or {}).get("mappings") or {})
    properties = previous_mapping.get("properties") or {}
    if "text_content" not in properties or "vector" not in properties:
        raise RuntimeError("active knowledge index mapping is missing text_content/vector")
    probe_index = f"rha-knowledge-v3-probe-{run_suffix}"
    elasticsearch.request_json("PUT", "/" + quote(probe_index, safe=""), {"mappings": previous_mapping})
    switched_indices: list[str] = []
    rollback_indices: list[str] = []
    readback_verified = False
    switch_attempted = False
    try:
        switch_attempted = True
        elasticsearch.request_json("POST", "/_aliases", {"actions": [
            {"remove": {"index": "*", "alias": alias_name, "must_exist": False}},
            {"add": {"index": probe_index, "alias": alias_name}},
        ]})
        switched = elasticsearch.request_json("GET", "/_alias/" + quote(alias_name, safe=""))
        switched_indices = sorted(switched.body)
        if switched_indices != [probe_index]:
            raise RuntimeError(f"alias probe switch readback mismatch: {switched_indices}")
        probe_id = "alias-probe-" + run_suffix
        elasticsearch.request_json("PUT", "/" + quote(alias_name, safe="") + "/_doc/" + probe_id + "?refresh=true", {
            "text_content": "alias migration readback " + run_suffix,
            "vector": [1.0] + [0.0] * 7,
        })
        readback = elasticsearch.request_json("GET", "/" + quote(alias_name, safe="") + "/_doc/" + probe_id)
        readback_verified = bool((readback.body or {}).get("found"))
        if not readback_verified:
            raise RuntimeError("alias probe document readback failed")
    finally:
        if switch_attempted:
            elasticsearch.request_json("POST", "/_aliases", {"actions": [
                {"remove": {"index": "*", "alias": alias_name, "must_exist": False}},
                {"add": {"index": previous_index, "alias": alias_name}},
            ]})
            rollback = elasticsearch.request_json("GET", "/_alias/" + quote(alias_name, safe=""))
            rollback_indices = sorted(rollback.body)
            if rollback_indices != [previous_index]:
                raise RuntimeError(f"alias rollback readback mismatch: {rollback_indices}")
    return alias_response, alias_indices, {
        "previousIndex": previous_index,
        "newIndex": probe_index,
        "mappingVerified": True,
        "readbackVerified": readback_verified,
        "switchedIndices": switched_indices,
        "rollbackIndices": rollback_indices,
    }


def run_runtime(
    args: argparse.Namespace,
    *,
    trace_id: str = "",
    websocket_connect: Callable[..., Any] | None = None,
) -> int:
    validate_runtime_args(args)
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
        exercise_resume: bool = False,
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

        resume_check: dict[str, Any] | None = None
        if exercise_resume:
            if len(chunks) < 2:
                raise RuntimeError("resume exercise requires at least two chunks")
            upload_chunk(0)
            checked = client.request_json("POST", "/api/v1/upload/check", {"md5": file_md5}, token=token)
            resume_check = {
                "source": "POST /api/v1/upload/check",
                "statusCode": checked.status_code,
                "completed": checked.body.get("completed"),
                "uploadedChunks": checked.body.get("uploadedChunks") or [],
            }
            for index in range(1, len(chunks)):
                upload_chunk(index)
        else:
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

        if wait_mode == "observed":
            pipeline_status = fetch_pipeline_status()
            poll_count = 1
        elif wait_mode == "dead_letter":
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
                "merge": {
                    "statusCode": merge.status_code,
                    "traceId": _response_header(merge, "X-Trace-ID"),
                },
                **({"resumeCheck": resume_check} if resume_check is not None else {}),
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
        build_minimal_pptx(f"RHA retention period is seven years. Runtime evidence marker: {run_suffix}.", resume_padding_bytes=5 * 1024 * 1024),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        exercise_resume=True,
    )
    image_query = "RHA image inspection code"
    image_upload, image_pipeline = upload_and_wait(
        f"rha-image-{run_suffix}.png",
        build_minimal_png(320, 120, marker=run_suffix),
        "image/png",
    )
    word_upload, word_pipeline = upload_and_wait(f"rha-word-{run_suffix}.docx", build_minimal_docx(run_suffix), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    excel_upload, excel_pipeline = upload_and_wait(f"rha-excel-{run_suffix}.xlsx", build_minimal_xlsx(run_suffix), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    pdf_upload, pdf_pipeline = upload_and_wait(f"rha-pdf-{run_suffix}.pdf", b"%PDF-1.4\n% RHA runtime MinerU input " + run_suffix.encode(), "application/pdf")

    alias_name = "rha-knowledge-active"
    elasticsearch = RuntimeHTTPClient(
        args.elasticsearch_url,
        trace_id,
        timeout_seconds=args.request_timeout,
    )
    alias_response, alias_indices, alias_migration = exercise_alias_migration(
        elasticsearch, alias_name, run_suffix
    )

    modality_paths = {
        "ppt": (upload, pipeline),
        "word": (word_upload, word_pipeline),
        "excel": (excel_upload, excel_pipeline),
        "pdf": (pdf_upload, pdf_pipeline),
        "image": (image_upload, image_pipeline),
    }
    evidence_documents: list[dict[str, Any]] = []
    for modality, (_, modality_pipeline) in modality_paths.items():
        version = str(modality_pipeline.get("documentVersion") or "")
        result = elasticsearch.request_json("POST", "/rha-evidence-active/_search", {
            "size": 1000,
            "query": {"term": {"document_version": version}},
        })
        hits = ((result.body.get("hits") or {}).get("hits") or [])
        if not hits:
            raise RuntimeError(f"no Elasticsearch evidence readback for {modality} version {version}")
        for hit in hits:
            source = hit.get("_source") or {}
            if source.get("modality") != modality or not source.get("source_asset"):
                raise RuntimeError(f"invalid {modality} evidence provenance readback")
            has_location = bool(source.get("page_number") or source.get("slide_number") or source.get("sheet_name") or source.get("heading_path") or source.get("bbox"))
            if not has_location:
                raise RuntimeError(f"{modality} evidence lacks page-or-equivalent location")
            evidence_documents.append(source)
    multimodal_evidence = {
        "source": "POST /rha-evidence-active/_search",
        "modalities": sorted(modality_paths),
        "total": len(evidence_documents),
        "counts": {modality: sum(1 for item in evidence_documents if item.get("modality") == modality) for modality in modality_paths},
        "allVersioned": all(bool(item.get("document_version")) for item in evidence_documents),
        "allLocated": True,
        "allDurableAssets": all(str(item.get("source_asset", "")).startswith("merged/") for item in evidence_documents),
    }

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

    broker_outage: dict[str, Any] | None = None
    if bool(getattr(args, "exercise_broker_outage", False)):
        broker_file = f"rha-broker-outage-{run_suffix}.txt"
        broker_contents = f"RHA broker outage recovery marker {run_suffix}.".encode("utf-8")
        broker_md5 = hashlib.md5(broker_contents).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{32}", broker_md5):
            raise RuntimeError("broker outage file identity is not a lowercase MD5 digest")
        db_password = os.environ.get("RHA_E2E_PASSWORD", "")
        if not db_password:
            raise RuntimeError("RHA_E2E_PASSWORD is required for broker outage outbox readback")
        outbox_query = (
            "SELECT publication_status, publication_attempt_count, attempt_count, published_at, publication_last_error "
            "FROM pipeline_task "
            f"WHERE file_md5='{broker_md5}' AND stage='parse' ORDER BY id DESC LIMIT 1;"
        )

        def read_outbox() -> list[str]:
            output = _docker_run(
                [
                    "docker", "exec", "-e", "MYSQL_PWD=" + db_password,
                    str(getattr(args, "mysql_container", "rha-e2e-mysql-1")),
                    "mysql", "-N", "-uroot", "RHA", "-e", outbox_query,
                ],
                timeout_seconds=args.request_timeout,
            ).stdout
            return parse_mysql_tsv_row(output)

        with kafka_broker_outage(
            str(getattr(args, "kafka_container", "rha-e2e-kafka-1")),
            bootstrap_server=str(getattr(args, "kafka_bootstrap_server", "kafka:29092")),
            timeout_seconds=args.pipeline_timeout,
            poll_interval=args.poll_interval,
        ) as broker_state:
            broker_upload, _ = upload_and_wait(
                broker_file,
                broker_contents,
                "text/plain",
                wait_mode="observed",
            )
            before_state, _ = wait_for_outbox_state(
                read_outbox,
                phase="before-recovery",
                timeout_seconds=args.pipeline_timeout,
                poll_interval=before_recovery_outbox_poll_interval(args.poll_interval),
            )

        broker_pipeline, broker_poll_count = wait_for_searchable(
            lambda: _response_data(
                client.request_json(
                    "GET",
                    "/api/v1/documents/pipeline-status?" + urlencode({"fileMd5": broker_md5}),
                    token=token,
                ),
                "broker outage pipeline status",
            ),
            timeout_seconds=args.pipeline_timeout,
            poll_interval=args.poll_interval,
        )
        after_state, _ = wait_for_outbox_state(
            read_outbox,
            phase="after-recovery",
            previous_publication_attempt_count=before_state["publicationAttemptCount"],
            timeout_seconds=args.pipeline_timeout,
            poll_interval=args.poll_interval,
        )
        broker_retrieval, broker_websocket = retrieve_and_chat(
            "RHA broker outage recovery marker"
        )
        broker_elasticsearch = read_elasticsearch_uniqueness(elasticsearch, broker_md5)
        broker_outage = {
            "brokerStopped": broker_state["stopped"] == {"running": False, "ready": False},
            "outboxPersisted": True,
            "automaticRecovery": broker_state.get("started") == {"running": True, "ready": True},
            "mergeRequestCount": 1,
            "upload": broker_upload,
            "publicationBeforeRecovery": before_state,
            "publicationAfterRecovery": after_state,
            "pipeline": {**broker_pipeline, "pollCount": broker_poll_count},
            "retrieval": broker_retrieval,
            "websocket": broker_websocket,
            "elasticsearch": broker_elasticsearch,
        }

    reliability: dict[str, Any] | None = None
    if bool(getattr(args, "exercise_reliability", False)):
        profile = client.request_json("GET", "/api/v1/users/me", token=token)
        profile_data = _response_data(profile, "profile")
        user_id = int((profile_data or {}).get("id", 0))
        if user_id <= 0:
            raise RuntimeError("profile did not return an authenticated user id")
        control_url = str(getattr(args, "model_stub_control_url", "http://127.0.0.1:8010"))
        reranker_delay_ms = 500
        reranker_timeout_ms = 200
        reranker_control: dict[str, Any] = {}
        reranker_elapsed_ms = 0.0
        try:
            set_model_failures(control_url, embeddings=True, timeout_seconds=args.request_timeout)
            degraded_retrieval, _ = retrieve_and_chat(query)
            embedding_fallback = any(hit.get("fileMd5") == upload["fileMd5"] for hit in degraded_retrieval.get("hits") or [])

            reranker_control = set_model_failures(
                control_url,
                reranker_delay_ms=reranker_delay_ms,
                timeout_seconds=args.request_timeout,
            )
            reranker_started = time.monotonic()
            rerank_search = client.request_json(
                "GET",
                "/api/v1/search/hybrid?" + urlencode({"query": query, "topK": 5}),
                token=token,
            )
            reranker_elapsed_ms = round((time.monotonic() - reranker_started) * 1000, 3)
            rerank_hits = _response_data(rerank_search, "reranker timeout search")
            if not isinstance(rerank_hits, list):
                raise RuntimeError("reranker timeout search response data must be a list")
            reranker_fallback = any(hit.get("fileMd5") == upload["fileMd5"] for hit in rerank_hits)
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
        foreign_retrieval, foreign_websocket = retrieve_and_chat(foreign_marker)
        foreign_hits = foreign_retrieval.get("hits") or []
        permitted_hit = any(hit.get("fileMd5") == upload["fileMd5"] for hit in retrieval.get("hits") or [])
        foreign_private_absent = not any(hit.get("fileMd5") == foreign_id for hit in foreign_hits)
        marker = "RHA-MEMORY-" + run_suffix
        db_password = os.environ.get("RHA_E2E_PASSWORD", "")

        def read_memory_index_row() -> list[str]:
            completed = _docker_run(
                [
                    "docker", "exec", "-e", "MYSQL_PWD=" + db_password,
                    str(args.mysql_container), "mysql", "-N", "-uroot", "RHA", "-e",
                    "SELECT index_status,index_attempt_count,"
                    "index_claimed_at IS NOT NULL,index_next_attempt_at IS NOT NULL,"
                    "index_last_error <> '',indexed_at IS NOT NULL "
                    f"FROM long_term_memories WHERE user_id={user_id} "
                    f"AND (content LIKE '%{marker}%' OR summary LIKE '%{marker}%') "
                    "ORDER BY id DESC LIMIT 1;",
                ],
                timeout_seconds=args.request_timeout,
            )
            return parse_mysql_tsv_row(completed.stdout)

        set_model_failures(control_url, embeddings=True, timeout_seconds=args.request_timeout)
        try:
            _, memory_first = retrieve_and_chat("Remember this durable project marker: " + marker)
            memory_before_recovery, _ = wait_for_memory_index_state(
                read_memory_index_row,
                phase="before-recovery",
                timeout_seconds=args.pipeline_timeout,
                poll_interval=args.poll_interval,
            )
        finally:
            set_model_failures(control_url, timeout_seconds=args.request_timeout)
        memory_after_recovery, _ = wait_for_memory_index_state(
            read_memory_index_row,
            phase="after-recovery",
            previous_attempt_count=memory_before_recovery["attemptCount"],
            timeout_seconds=args.pipeline_timeout,
            poll_interval=args.poll_interval,
        )
        memory_mapping = read_memory_index_mapping(elasticsearch, "conversation_memory")

        memory_deadline = time.monotonic() + args.pipeline_timeout
        mysql_count = 0
        es_memory_count = 0
        while time.monotonic() < memory_deadline:
            completed = subprocess.run(
                ["docker", "exec", "-e", "MYSQL_PWD=" + db_password, str(args.mysql_container),
                 "mysql", "-N", "-uroot", "RHA", "-e",
                 f"SELECT COUNT(*) FROM long_term_memories WHERE user_id={user_id} "
                 f"AND (content LIKE '%{marker}%' OR summary LIKE '%{marker}%');"],
                check=False, capture_output=True, text=True,
            )
            if completed.returncode == 0 and completed.stdout.strip().isdigit():
                mysql_count = int(completed.stdout.strip())
            try:
                es_count_response = elasticsearch.request_json(
                    "POST", "/conversation_memory/_count",
                    {"query": {"bool": {"filter": [{"term": {"user_id": user_id}}],
                                         "must": [{"match_phrase": {"text_content": marker}}]}}},
                )
                es_memory_count = int((es_count_response.body or {}).get("count", 0))
            except RuntimeError:
                es_memory_count = 0
            if mysql_count > 0 and mysql_count == es_memory_count:
                break
            time.sleep(max(0.1, args.poll_interval))
        if mysql_count < 1 or mysql_count != es_memory_count:
            raise RuntimeError(
                "memory marker counts did not converge between MySQL and Elasticsearch: "
                f"mysql={mysql_count} elasticsearch={es_memory_count}"
            )
        redis_password = os.environ.get("RHA_E2E_PASSWORD", "")
        redis_clear = clear_redis_conversation_history(
            str(getattr(args, "redis_container", "rha-e2e-redis-1")),
            redis_password,
        )
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
        memory_items = (memory_readback or {}).get("items") or []
        foreign_citations = foreign_websocket.get("citations") or []
        foreign_answer = str(foreign_websocket.get("answer", ""))
        reliability = {
            "degradation": {
                "embeddingFailureFallback": embedding_fallback,
                "rerankerTimeoutFallback": reranker_fallback,
                "embeddingFileMd5": upload["fileMd5"],
                "rerankerFileMd5": upload["fileMd5"],
                "rerankerControl": {
                    "requestedDelayMs": reranker_delay_ms,
                    "readbackDelayMs": reranker_control.get("reranker_delay_ms"),
                    "configuredTimeoutMs": reranker_timeout_ms,
                    "requestElapsedMs": reranker_elapsed_ms,
                    "returnedBeforeDelay": reranker_elapsed_ms < reranker_delay_ms,
                },
            },
            "permission": {
                "permittedHit": permitted_hit,
                "foreignPrivateAbsent": foreign_private_absent,
                "citationsFiltered": all(citation.get("evidenceId") != foreign_id for citation in foreign_citations),
                "answerFiltered": foreign_marker not in foreign_answer and foreign_id not in foreign_answer,
                "foreignDocumentId": foreign_id,
                "foreignMarker": foreign_marker,
                "retrieval": foreign_retrieval,
                "websocket": foreign_websocket,
            },
            "memory": {
                "marker": marker,
                "firstTurnStored": mysql_count > 0,
                "secondTurnRetrieved": marker in str(memory_second.get("answer", "")),
                "durable": mysql_count > 0 and mysql_count == es_memory_count,
                "mysqlMarkerCount": mysql_count,
                "elasticsearchMarkerCount": es_memory_count,
                "indexing": {
                    "beforeRecovery": memory_before_recovery,
                    "afterRecovery": memory_after_recovery,
                    "mapping": memory_mapping,
                },
                "shortTermHistoryCleared": redis_clear["cleared"],
                "redisKeysBefore": redis_clear["keysBefore"],
                "redisKeysAfter": redis_clear["keysAfter"],
                "readbackItems": memory_items,
                "turns": [memory_first, memory_second],
            },
            "trace": {"events": (memory_first.get("events") or []) + (memory_second.get("events") or [])},
            "graph": {
                "nodes": graph_contract.get("nodes") or [],
                "edges": graph_contract.get("edges") or [],
            },
        }
        if broker_outage is not None:
            reliability["brokerOutage"] = broker_outage

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
            "aliasMigration": alias_migration,
        },
        "retrieval": retrieval,
        "websocket": websocket,
        "image": {
            "upload": image_upload,
            "pipeline": image_pipeline,
            "retrieval": image_retrieval,
            "websocket": image_websocket,
        },
        "multimodalEvidence": multimodal_evidence,
    }
    if recovery is not None:
        report["recovery"] = recovery
    if reliability is not None:
        report["reliability"] = reliability
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output_path.write_bytes(report_bytes)
    integrity_path = output_path.with_suffix(output_path.suffix + ".integrity.json")
    integrity = {
        "kind": "rha-runtime-runner-integrity-binding",
        "reportSha256": hashlib.sha256(report_bytes).hexdigest(),
        "runnerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runner": "scripts/rha_runtime_e2e.py",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "freshDockerRunRequired": True,
        "assurance": "sha256-integrity-only",
    }
    integrity_path.write_text(json.dumps(integrity, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    args = build_argument_parser().parse_args()
    return run_runtime(args)


if __name__ == "__main__":
    raise SystemExit(main())

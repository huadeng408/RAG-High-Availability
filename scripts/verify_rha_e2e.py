#!/usr/bin/env python3
"""Validate the small deterministic RHA E2E report contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_STAGES = {"parse", "chunk", "embed", "index"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}


def _field(label: str, name: str) -> str:
    return f"{label}.{name}" if label else name


def _verify_document_path(
    *,
    label: str,
    upload: dict,
    pipeline: dict,
    retrieval: dict,
    websocket: dict,
    trace_id: str,
    answer: dict | None = None,
) -> tuple[dict, dict]:
    chunk_requests = upload.get("chunkRequests") or []
    successful_indexes = [
        item.get("chunkIndex")
        for item in chunk_requests
        if item.get("statusCode") == 200
    ]
    if len(successful_indexes) < 2 or len(set(successful_indexes)) == len(successful_indexes):
        raise ValueError(
            f"{_field(label, 'upload.chunkRequests')} must include a successful duplicate chunk request"
        )
    if (upload.get("merge") or {}).get("statusCode") != 200:
        raise ValueError(f"{_field(label, 'upload.merge.statusCode')} must be 200")
    file_md5 = str(upload.get("fileMd5", ""))
    if not re.fullmatch(r"[0-9a-f]{32}", file_md5):
        raise ValueError(f"{_field(label, 'upload.fileMd5')} must be a lowercase MD5 digest")
    if pipeline.get("status") != "SEARCHABLE":
        raise ValueError(f"{_field(label, 'pipeline.status')} must be SEARCHABLE")
    if not pipeline.get("documentVersion"):
        raise ValueError(f"{_field(label, 'pipeline.documentVersion')} is required")
    stages = {item.get("stage"): item for item in pipeline.get("stages") or []}
    if set(stages) != REQUIRED_STAGES or any(
        stages[stage].get("status") != "SUCCESS"
        or int(stages[stage].get("attemptCount", 0)) < 1
        for stage in REQUIRED_STAGES
    ):
        raise ValueError(
            f"{_field(label, 'pipeline.stages')} must contain four successful runtime stages"
        )
    if retrieval.get("statusCode") != 200 or not retrieval.get("hits"):
        raise ValueError(
            f"{_field(label, 'retrieval.hits')} must contain a successful runtime search result"
        )
    current_hits = [
        hit for hit in retrieval["hits"]
        if hit.get("fileMd5") == file_md5
    ]
    if not current_hits:
        raise ValueError(f"{_field(label, 'retrieval.hits')} must contain the uploaded file")
    if retrieval.get("traceId") != trace_id or websocket.get("traceId") != trace_id:
        raise ValueError(
            f"{_field(label, 'traceId')} must match across retrieval and websocket observations"
        )
    if not str(websocket.get("answer", "")).strip():
        raise ValueError(
            f"{_field(label, 'websocket.answer')} must contain streamed response text"
        )
    citations = websocket.get("citations") or (answer or {}).get("citations") or []
    if not citations:
        raise ValueError(
            f"{_field(label, 'websocket.citations')} must contain a source-level citation"
        )
    current_citations = [
        citation for citation in citations
        if citation.get("documentVersion") == pipeline.get("documentVersion")
    ]
    if not current_citations:
        raise ValueError(
            f"{_field(label, 'websocket.citations')} must include the current documentVersion"
        )
    citation = current_citations[0]
    has_location = (
        int(citation.get("page", 0)) > 0
        or int(citation.get("slide", 0)) > 0
        or bool(citation.get("sheet"))
        or bool(citation.get("bbox"))
    )
    if not has_location or not citation.get("evidenceId"):
        raise ValueError(
            f"{_field(label, 'websocket.citations')} must include evidenceId and a source location"
        )
    retrieval_citations = [
        item
        for hit in current_hits
        for item in hit.get("citations") or []
    ]
    citation_key = (citation.get("evidenceId"), citation.get("documentVersion"))
    retrieval_citation = next(
        (
            item
            for item in retrieval_citations
            if (item.get("evidenceId"), item.get("documentVersion")) == citation_key
        ),
        None,
    )
    if retrieval_citation is None:
        raise ValueError(
            f"{_field(label, 'websocket citation')} must match a retrieval citation"
        )
    return citation, retrieval_citation


def _verify_image_citation(citation: dict, *, field_name: str) -> None:
    if citation.get("modality") != "image":
        raise ValueError(f"{field_name}.modality must be modality=image")
    bbox = citation.get("bbox") or {}
    coordinates = [bbox.get(name) for name in ("x0", "y0", "x1", "y1")]
    if (
        any(type(value) not in (int, float) for value in coordinates)
        or coordinates[0] < 0
        or coordinates[1] < 0
        or coordinates[2] <= coordinates[0]
        or coordinates[3] <= coordinates[1]
    ):
        raise ValueError(f"{field_name}.bbox must contain a positive pixel region")
    image = citation.get("image") or {}
    width = image.get("width")
    height = image.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError(f"{field_name} must include positive pixel width and height metadata")
    if coordinates[2] > width or coordinates[3] > height:
        raise ValueError(f"{field_name}.bbox must stay inside the image pixel dimensions")
    if not re.fullmatch(r"[0-9a-f]{64}", str(image.get("assetSha256", ""))):
        raise ValueError(f"{field_name}.image.assetSha256 must be a lowercase SHA-256 digest")
    if image.get("mimeType") not in IMAGE_MIME_TYPES:
        raise ValueError(f"{field_name}.image.mimeType must identify a supported image")


def _verify_recovery(report: dict) -> None:
    recovery = report.get("recovery")
    if not isinstance(recovery, dict):
        raise ValueError("recovery object is required for schema v3")
    stage = str(recovery.get("stage", "")).strip()
    if stage != "embed":
        raise ValueError("recovery.stage must identify the failed embed stage")
    dlq_id = str(recovery.get("dlqMessageId", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", dlq_id):
        raise ValueError("recovery.dlqMessageId must be a lowercase SHA-256 digest")
    envelope = recovery.get("dlq") or {}
    if envelope.get("topic") != "file-dlq" or envelope.get("messageId") != dlq_id:
        raise ValueError("recovery DLQ envelope message ID must match recovery.dlqMessageId")
    payload = envelope.get("payload") or {}
    if payload.get("stage") != stage or not (payload.get("fileMd5") or payload.get("file_md5")):
        raise ValueError("recovery DLQ payload must identify the failed stage and file")

    replay = recovery.get("replay") or {}
    replay_ids = [str(value) for value in replay.get("messageIds") or []]
    if (
        replay.get("statusCode") != 200
        or int(replay.get("replayedTasks", 0)) != 1
        or replay_ids != [dlq_id]
    ):
        raise ValueError("recovery replay result must acknowledge exactly the selected DLQ task")

    pipeline = recovery.get("pipeline") or {}
    if pipeline.get("status") != "SEARCHABLE":
        raise ValueError("recovery replay pipeline must become SEARCHABLE")
    if not pipeline.get("documentVersion"):
        raise ValueError("recovery replay pipeline documentVersion is required")
    stages = {item.get("stage"): item for item in pipeline.get("stages") or []}
    embed = stages.get(stage) or {}
    if embed.get("status") != "SUCCESS" or int(embed.get("replayCount", 0)) < 1:
        raise ValueError("recovery replay stage must be successful with replay metadata")

    counts = recovery.get("elasticsearch") or {}
    if int(counts.get("knowledgeCount", 0)) != 1 or int(counts.get("evidenceCount", 0)) != 1:
        raise ValueError("recovery Elasticsearch counts prove duplicate knowledge/evidence was created")


def verify(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("reportKind") != "rha-runtime-e2e":
        raise ValueError("reportKind must identify a runtime E2E report")
    auth = report.get("auth") or {}
    if auth.get("tokenAcquired") is not True:
        raise ValueError("auth.tokenAcquired must be true after a runtime login")
    trace_id = report.get("traceId")
    if not trace_id:
        raise ValueError("traceId is required")

    pipeline = report.get("pipeline") or {}
    _verify_document_path(
        label="",
        upload=report.get("upload") or {},
        pipeline=pipeline,
        retrieval=report.get("retrieval") or {},
        websocket=report.get("websocket") or {},
        trace_id=trace_id,
        answer=report.get("answer") or {},
    )
    if pipeline.get("alias") != "rha-knowledge-active":
        raise ValueError("pipeline.alias must point at rha-knowledge-active")
    alias_readback = pipeline.get("aliasReadback") or {}
    if (
        alias_readback.get("source") != "GET /_alias/rha-knowledge-active"
        or alias_readback.get("statusCode") != 200
        or not alias_readback.get("indices")
    ):
        raise ValueError("pipeline.aliasReadback must contain a successful Elasticsearch alias readback")

    schema_version = report.get("schemaVersion")
    if schema_version not in (2, 3):
        raise ValueError("schemaVersion must be 2 or 3 for the image runtime contract")
    image_path = report.get("image")
    if not isinstance(image_path, dict):
        raise ValueError("image runtime path is required")
    image_citation, retrieval_image_citation = _verify_document_path(
        label="image",
        upload=image_path.get("upload") or {},
        pipeline=image_path.get("pipeline") or {},
        retrieval=image_path.get("retrieval") or {},
        websocket=image_path.get("websocket") or {},
        trace_id=trace_id,
    )
    if "IMG-2048" not in str(image_path["websocket"].get("answer", "")):
        raise ValueError("image.websocket.answer must contain the OCR fact IMG-2048")
    _verify_image_citation(image_citation, field_name="image.websocket.citations[0]")
    _verify_image_citation(retrieval_image_citation, field_name="image.retrieval.citations[0]")
    if image_citation != retrieval_image_citation:
        raise ValueError("image.websocket citation must equal its retrieval citation")
    if schema_version == 3:
        _verify_recovery(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(args.report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"RHA E2E verification failed: {exc}")
        return 1
    citations = (report.get("websocket") or {}).get("citations") or (report.get("answer") or {}).get("citations")
    image_citations = report["image"]["websocket"]["citations"]
    print(
        "RHA E2E verified: "
        f"version={report['pipeline']['documentVersion']} evidence={citations[0]['evidenceId']} "
        f"image_version={report['image']['pipeline']['documentVersion']} "
        f"image_evidence={image_citations[0]['evidenceId']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

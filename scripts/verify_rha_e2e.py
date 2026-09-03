#!/usr/bin/env python3
"""Validate the small deterministic RHA E2E report contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_STAGES = {"parse", "chunk", "embed", "index"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
GRAPH_NODES = [
    "load_history", "classify_intent", "rewrite_query", "prepare_prompt_context",
    "retrieve_knowledge", "retrieve_memory", "fuse_context", "rerank_context",
    "build_messages", "generate_answer", "persist_memory",
]


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
    merge = upload.get("merge") or {}
    if merge.get("statusCode") != 200:
        raise ValueError(f"{_field(label, 'upload.merge.statusCode')} must be 200")
    merge_trace_id = str(merge.get("traceId", "")).strip()
    if not merge_trace_id:
        raise ValueError(f"{_field(label, 'upload.merge.traceId')} is required")
    if not label:
        resume = upload.get("resumeCheck") or {}
        if (
            resume.get("source") != "POST /api/v1/upload/check"
            or resume.get("statusCode") != 200
            or resume.get("completed") is not False
            or resume.get("uploadedChunks") != [0]
        ):
            raise ValueError("upload.resumeCheck must prove an interrupted upload resumed after chunk 0")
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
    if any(str(stages[stage].get("lastTraceId", "")).strip() != merge_trace_id for stage in REQUIRED_STAGES):
        raise ValueError(
            f"{_field(label, 'pipeline.stages')} must preserve the upload merge traceId"
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
    retrieval_trace_id = str(retrieval.get("traceId", "")).strip()
    websocket_trace_id = str(websocket.get("traceId", "")).strip()
    if not retrieval_trace_id or not websocket_trace_id:
        raise ValueError(f"{_field(label, 'traceId')} is required for retrieval and websocket observations")
    websocket_events = websocket.get("events")
    if not isinstance(websocket_events, list) or not websocket_events:
        raise ValueError(f"{_field(label, 'websocket.events')} must contain raw stream events")
    if any(
        not isinstance(event, dict)
        or str(event.get("traceId", "")).strip() != websocket_trace_id
        for event in websocket_events
    ):
        raise ValueError(f"{_field(label, 'websocket.events')} must preserve its stream traceId")
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
    retrieval_citations = [
        item
        for hit in current_hits
        for item in hit.get("citations") or []
    ]
    retrieval_keys = {
        (item.get("evidenceId"), item.get("documentVersion"))
        for item in retrieval_citations
    }
    citation = next(
        (
            item
            for item in current_citations
            if (item.get("evidenceId"), item.get("documentVersion")) in retrieval_keys
        ),
        None,
    )
    if citation is None:
        raise ValueError(
            f"{_field(label, 'websocket citation')} must match a retrieval citation"
        )
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
    citation_key = (citation.get("evidenceId"), citation.get("documentVersion"))
    retrieval_citation = next(
        (
            item
            for item in retrieval_citations
            if (item.get("evidenceId"), item.get("documentVersion")) == citation_key
        ),
        None,
    )
    assert retrieval_citation is not None
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
    recovery_upload = recovery.get("upload") or {}
    expected_file_md5 = str(recovery_upload.get("fileMd5", ""))
    if payload.get("stage") != stage:
        raise ValueError("recovery DLQ payload must identify the failed stage")
    payload_dlq_id = str(payload.get("dlq_id") or payload.get("dlqMessageId") or "")
    if payload_dlq_id != dlq_id:
        raise ValueError("recovery DLQ payload dlq_id must match recovery.dlqMessageId")
    payload_file_md5 = str(payload.get("fileMd5") or payload.get("file_md5") or "")
    if not expected_file_md5 or payload_file_md5 != expected_file_md5:
        raise ValueError("recovery DLQ payload fileMd5 must match the selected recovery upload")

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
    expected_document_version = str(pipeline.get("documentVersion", ""))
    if not expected_document_version:
        raise ValueError("recovery replay pipeline documentVersion is required")
    payload_document_version = str(payload.get("documentVersion") or payload.get("document_version") or "")
    if payload_document_version != expected_document_version:
        raise ValueError("recovery DLQ payload documentVersion must match the replay pipeline")
    stages = {item.get("stage"): item for item in pipeline.get("stages") or []}
    if set(stages) != REQUIRED_STAGES or any(
        stages[required].get("status") != "SUCCESS" or int(stages[required].get("attemptCount", 0)) < 1
        for required in REQUIRED_STAGES
    ):
        raise ValueError("recovery pipeline.stages must contain four successful runtime stages")
    recovery_merge_trace = str(((recovery_upload.get("merge") or {}).get("traceId")) or "").strip()
    if not recovery_merge_trace or any(
        str(stages[required].get("lastTraceId", "")).strip() != recovery_merge_trace
        for required in REQUIRED_STAGES
    ):
        raise ValueError("recovery pipeline stages must preserve the upload merge traceId")
    embed = stages.get(stage) or {}
    if embed.get("status") != "SUCCESS" or int(embed.get("replayCount", 0)) < 1:
        raise ValueError("recovery replay stage must be successful with replay metadata")

    counts = recovery.get("elasticsearch") or {}
    if int(counts.get("knowledgeCount", 0)) != 1 or int(counts.get("evidenceCount", 0)) != 1:
        raise ValueError("recovery Elasticsearch counts prove duplicate knowledge/evidence was created")


def _verify_reliability(report: dict) -> None:
    reliability = report.get("reliability")
    if not isinstance(reliability, dict):
        raise ValueError("reliability object is required for schema v4")

    broker = reliability.get("brokerOutage")
    if not isinstance(broker, dict):
        raise ValueError("reliability.brokerOutage is required")
    for key in ("brokerStopped", "outboxPersisted", "automaticRecovery"):
        if broker.get(key) is not True:
            raise ValueError(f"reliability.brokerOutage.{key} must be true")
    if broker.get("mergeRequestCount") != 1:
        raise ValueError("reliability.brokerOutage.mergeRequestCount must be exactly one")
    upload = broker.get("upload") or {}
    file_md5 = str(upload.get("fileMd5", ""))
    if not re.fullmatch(r"[0-9a-f]{32}", file_md5):
        raise ValueError("reliability.brokerOutage.upload.fileMd5 must be a lowercase MD5 digest")
    merge = upload.get("merge") or {}
    if merge.get("statusCode") != 200 or not str(merge.get("traceId", "")).strip():
        raise ValueError("reliability.brokerOutage.upload.merge must prove one successful merge")
    before = broker.get("publicationBeforeRecovery") or {}
    after = broker.get("publicationAfterRecovery") or {}
    before_attempts = before.get("publicationAttemptCount")
    after_attempts = after.get("publicationAttemptCount")
    if (
        before.get("status") != "PENDING"
        or type(before_attempts) is not int or before_attempts < 1
        or before.get("processingAttemptCount") != 0
        or before.get("published") is not False
        or before.get("lastErrorPresent") is not True
    ):
        raise ValueError("reliability.brokerOutage before-recovery publication state is invalid")
    if (
        after.get("status") != "PUBLISHED"
        or type(after_attempts) is not int or after_attempts <= before_attempts
        or type(after.get("processingAttemptCount")) is not int or after["processingAttemptCount"] < 1
        or after.get("published") is not True
        or after.get("lastErrorPresent") is not False
    ):
        raise ValueError("reliability.brokerOutage after-recovery publication state is invalid")
    pipeline = broker.get("pipeline") or {}
    document_version = str(pipeline.get("documentVersion", "")).strip()
    if pipeline.get("status") != "SEARCHABLE" or not document_version or document_version == "upload:" + file_md5:
        raise ValueError("reliability.brokerOutage pipeline must become SEARCHABLE with an immutable version")
    stages = {item.get("stage"): item for item in pipeline.get("stages") or []}
    if set(stages) != REQUIRED_STAGES or any(
        stages[name].get("status") != "SUCCESS" or int(stages[name].get("attemptCount", 0)) < 1
        for name in REQUIRED_STAGES
    ):
        raise ValueError("reliability.brokerOutage pipeline must contain four successful stages")
    retrieval = broker.get("retrieval") or {}
    hits = retrieval.get("hits")
    matching_hits = [hit for hit in hits or [] if hit.get("fileMd5") == file_md5 and hit.get("documentVersion") == document_version]
    websocket = broker.get("websocket") or {}
    if not matching_hits:
        raise ValueError("reliability.brokerOutage retrieval must contain the recovered file and version")
    if not str(websocket.get("answer", "")).strip():
        raise ValueError("reliability.brokerOutage websocket answer must be non-empty")
    retrieval_evidence = {citation.get("evidenceId") for hit in matching_hits for citation in hit.get("citations") or [] if citation.get("evidenceId")}
    websocket_evidence = {citation.get("evidenceId") for citation in websocket.get("citations") or [] if citation.get("evidenceId")}
    if not retrieval_evidence.intersection(websocket_evidence):
        raise ValueError("reliability.brokerOutage websocket citations must intersect retrieval evidence")
    counts = broker.get("elasticsearch") or {}
    knowledge = counts.get("knowledgeCount")
    evidence = counts.get("evidenceCount")
    if (
        type(knowledge) is not int or knowledge < 1 or counts.get("uniqueKnowledgeUnits") != knowledge
        or type(evidence) is not int or evidence < 1 or counts.get("uniqueEvidenceUnits") != evidence
    ):
        raise ValueError("reliability.brokerOutage Elasticsearch counts must be positive and duplicate-free")
    identity_contracts = (
        ("knowledgeIds", knowledge),
        ("evidenceIds", evidence),
    )
    for field, expected_count in identity_contracts:
        identities = counts.get(field)
        if (
            not isinstance(identities, list)
            or len(identities) != expected_count
            or len(identities) != len(set(identities))
            or any(not str(identity).strip() for identity in identities)
        ):
            raise ValueError(
                f"reliability.brokerOutage Elasticsearch {field} must contain all unique persisted identities"
            )

    degradation = reliability.get("degradation")
    if not isinstance(degradation, dict):
        raise ValueError("reliability.degradation is required")
    for key in ("embeddingFailureFallback", "rerankerTimeoutFallback"):
        if degradation.get(key) is not True:
            raise ValueError(f"reliability.degradation.{key} must be true")
    reranker_control = degradation.get("rerankerControl")
    if not isinstance(reranker_control, dict):
        raise ValueError("reliability.degradation.rerankerControl is required")
    requested_delay = reranker_control.get("requestedDelayMs")
    readback_delay = reranker_control.get("readbackDelayMs")
    configured_timeout = reranker_control.get("configuredTimeoutMs")
    request_elapsed = reranker_control.get("requestElapsedMs")
    if (
        requested_delay != 500
        or readback_delay != requested_delay
        or configured_timeout != 200
        or requested_delay <= configured_timeout
        or not isinstance(request_elapsed, (int, float))
        or request_elapsed < 0
        or request_elapsed >= requested_delay
        or reranker_control.get("returnedBeforeDelay") is not True
    ):
        raise ValueError("reranker timeout evidence must prove the 500 ms delay exceeded the 200 ms timeout")

    permission = reliability.get("permission")
    if not isinstance(permission, dict):
        raise ValueError("reliability.permission is required")
    for key in ("permittedHit", "foreignPrivateAbsent", "citationsFiltered", "answerFiltered"):
        if permission.get(key) is not True:
            raise ValueError(f"reliability.permission.{key} must be true")
    foreign_id = str(permission.get("foreignDocumentId", "")).strip()
    foreign_marker = str(permission.get("foreignMarker", "")).strip()
    foreign_retrieval = permission.get("retrieval")
    foreign_websocket = permission.get("websocket")
    if (
        not foreign_id
        or not foreign_marker
        or not isinstance(foreign_retrieval, dict)
        or not isinstance(foreign_retrieval.get("hits"), list)
        or not isinstance(foreign_websocket, dict)
        or not isinstance(foreign_websocket.get("citations"), list)
    ):
        raise ValueError("foreign permission evidence must include the marker-specific retrieval and chat")
    raw_permission_evidence = json.dumps(
        {"retrieval": foreign_retrieval, "websocket": foreign_websocket},
        ensure_ascii=False,
    )
    if foreign_id in raw_permission_evidence or foreign_marker in raw_permission_evidence:
        raise ValueError("foreign private document or marker leaked into retrieval, citations, or answer")

    memory = reliability.get("memory")
    if not isinstance(memory, dict) or not str(memory.get("marker", "")).strip():
        raise ValueError("reliability.memory.marker is required")
    for key in ("firstTurnStored", "secondTurnRetrieved", "durable"):
        if memory.get(key) is not True:
            raise ValueError(f"reliability.memory.{key} must be true")
    marker = str(memory["marker"]).strip()
    if int(memory.get("mysqlMarkerCount", 0)) < 1:
        raise ValueError("memory marker must be present in MySQL")
    if int(memory.get("elasticsearchMarkerCount", 0)) < 1:
        raise ValueError("memory marker must be present in Elasticsearch")
    readback_items = memory.get("readbackItems")
    if not isinstance(readback_items, list) or marker not in json.dumps(readback_items, ensure_ascii=False):
        raise ValueError("memory marker must be present in direct readback items")
    if memory.get("shortTermHistoryCleared") is not True:
        raise ValueError("reliability.memory.shortTermHistoryCleared must be true")
    if not isinstance(memory.get("redisKeysBefore"), list) or not memory["redisKeysBefore"]:
        raise ValueError("Redis deletion evidence must include keys found before deletion")
    if memory.get("redisKeysAfter") != []:
        raise ValueError("Redis deletion evidence must prove no conversation keys remained")

    turns = memory.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise ValueError("memory trace evidence must contain exactly two turns")
    flattened_events: list[dict] = []
    for turn_index, turn in enumerate(turns):
        if not isinstance(turn, dict) or not str(turn.get("traceId", "")).strip():
            raise ValueError(f"memory turn {turn_index} traceId is required")
        turn_trace_id = str(turn["traceId"]).strip()
        turn_events = turn.get("events")
        if not isinstance(turn_events, list) or not turn_events:
            raise ValueError(f"memory turn {turn_index} events are required")
        has_chunk = False
        has_completion = False
        for event in turn_events:
            if not isinstance(event, dict) or event.get("traceId") != turn_trace_id:
                raise ValueError(f"memory turn {turn_index} raw event traceId must match its turn traceId")
            has_chunk = has_chunk or event.get("type") == "chunk"
            has_completion = has_completion or (
                event.get("type") == "completion" and event.get("status") == "finished"
            )
        if not has_chunk:
            raise ValueError(f"memory turn {turn_index} must contain a chunk event")
        if not has_completion:
            raise ValueError(f"memory turn {turn_index} must contain a finished completion event")
        flattened_events.extend(turn_events)

    trace = reliability.get("trace")
    events = trace.get("events") if isinstance(trace, dict) else None
    if not isinstance(events, list) or not events:
        raise ValueError("reliability.trace.events is required")
    if events != flattened_events:
        raise ValueError("reliability trace events must exactly flatten the two raw memory turns")
    turn_trace_ids = {str(turn["traceId"]).strip() for turn in turns}
    for index, event in enumerate(events):
        if not isinstance(event, dict) or str(event.get("traceId", "")).strip() not in turn_trace_ids:
            raise ValueError(f"reliability.trace.events[{index}] traceId must match its source turn")
        if event.get("type") not in {"chunk", "trace", "error", "done", "completion"}:
            raise ValueError(f"reliability.trace.events[{index}] has invalid type")

    graph = reliability.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if (
        not isinstance(nodes, list)
        or len(nodes) != len(GRAPH_NODES)
        or set(nodes) != set(GRAPH_NODES)
    ):
        raise ValueError("reliability.graph.nodes must contain the exact 11-node graph")
    ordered = ["__start__", *GRAPH_NODES, "__end__"]
    edges = graph.get("edges")
    expected_edges = {(left, right) for left, right in zip(ordered, ordered[1:])}
    if (
        not isinstance(edges, list)
        or len(edges) != len(expected_edges)
        or any(not isinstance(edge, list) or len(edge) != 2 for edge in edges)
        or {tuple(edge) for edge in edges} != expected_edges
    ):
        raise ValueError("reliability.graph.edges must contain the exact linear graph")


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
    migration = pipeline.get("aliasMigration") or {}
    previous_index = migration.get("previousIndex")
    new_index = migration.get("newIndex")
    if (
        not previous_index
        or not new_index
        or previous_index == new_index
        or migration.get("mappingVerified") is not True
        or migration.get("readbackVerified") is not True
        or migration.get("switchedIndices") != [new_index]
        or migration.get("rollbackIndices") != [previous_index]
        or alias_readback.get("indices") != [previous_index]
    ):
        raise ValueError("pipeline.aliasMigration must prove mapping, switch readback, and rollback to the previous index")

    schema_version = report.get("schemaVersion")
    if schema_version not in (2, 3, 4):
        raise ValueError("schemaVersion must be 2, 3, or 4 for the image runtime contract")
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
    multimodal = report.get("multimodalEvidence") or {}
    counts = multimodal.get("counts") or {}
    required_modalities = {"pdf", "word", "ppt", "excel", "image"}
    if (
        multimodal.get("source") != "POST /rha-evidence-active/_search"
        or set(multimodal.get("modalities") or []) != required_modalities
        or set(counts) != required_modalities
        or any(type(counts.get(name)) is not int or counts[name] < 1 for name in required_modalities)
        or multimodal.get("total") != sum(counts.values())
        or multimodal.get("total", 0) < 54
        or multimodal.get("allVersioned") is not True
        or multimodal.get("allLocated") is not True
        or multimodal.get("allDurableAssets") is not True
    ):
        raise ValueError("multimodalEvidence must prove at least 54 located, versioned units across PDF, Word, PPT, Excel, and image")
    if schema_version in (3, 4):
        _verify_recovery(report)
    if schema_version == 4:
        _verify_reliability(report)
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

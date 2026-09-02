#!/usr/bin/env python3
"""Validate the small deterministic RHA E2E report contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("reportKind") != "rha-runtime-e2e":
        raise ValueError("reportKind must identify a runtime E2E report")
    auth = report.get("auth") or {}
    if auth.get("tokenAcquired") is not True:
        raise ValueError("auth.tokenAcquired must be true after a runtime login")
    upload = report.get("upload") or {}
    chunk_requests = upload.get("chunkRequests") or []
    successful_indexes = [
        item.get("chunkIndex")
        for item in chunk_requests
        if item.get("statusCode") == 200
    ]
    if len(successful_indexes) < 2 or len(set(successful_indexes)) == len(successful_indexes):
        raise ValueError("upload.chunkRequests must include a successful duplicate chunk request")
    if (upload.get("merge") or {}).get("statusCode") != 200:
        raise ValueError("upload.merge.statusCode must be 200")
    pipeline = report.get("pipeline") or {}
    answer = report.get("answer") or {}
    websocket = report.get("websocket") or {}
    if pipeline.get("status") != "SEARCHABLE":
        raise ValueError("pipeline.status must be SEARCHABLE")
    if not pipeline.get("documentVersion"):
        raise ValueError("pipeline.documentVersion is required")
    stages = {item.get("stage"): item for item in pipeline.get("stages") or []}
    required_stages = {"parse", "chunk", "embed", "index"}
    if set(stages) != required_stages or any(
        stages[stage].get("status") != "SUCCESS"
        or int(stages[stage].get("attemptCount", 0)) < 1
        for stage in required_stages
    ):
        raise ValueError("pipeline.stages must contain four successful runtime stages")
    if pipeline.get("alias") != "rha-knowledge-active":
        raise ValueError("pipeline.alias must point at rha-knowledge-active")
    alias_readback = pipeline.get("aliasReadback") or {}
    if (
        alias_readback.get("source") != "GET /_alias/rha-knowledge-active"
        or alias_readback.get("statusCode") != 200
        or not alias_readback.get("indices")
    ):
        raise ValueError("pipeline.aliasReadback must contain a successful Elasticsearch alias readback")
    trace_id = report.get("traceId")
    if not trace_id:
        raise ValueError("traceId is required")
    retrieval = report.get("retrieval") or {}
    if retrieval.get("statusCode") != 200 or not retrieval.get("hits"):
        raise ValueError("retrieval.hits must contain a successful runtime search result")
    current_hits = [
        hit for hit in retrieval["hits"]
        if hit.get("fileMd5") == upload.get("fileMd5")
    ]
    if not current_hits:
        raise ValueError("retrieval.hits must contain the uploaded file")
    if retrieval.get("traceId") != trace_id or websocket.get("traceId") != trace_id:
        raise ValueError("traceId must match across retrieval and websocket observations")
    if not str(websocket.get("answer", "")).strip():
        raise ValueError("websocket.answer must contain streamed response text")
    citations = websocket.get("citations") or answer.get("citations") or []
    if not citations:
        raise ValueError("websocket.citations must contain a source-level citation")
    current_citations = [
        citation for citation in citations
        if citation.get("documentVersion") == pipeline.get("documentVersion")
    ]
    if not current_citations:
        raise ValueError("websocket.citations must include the current documentVersion")
    citation = current_citations[0]
    has_location = (
        int(citation.get("page", 0)) > 0
        or int(citation.get("slide", 0)) > 0
        or bool(citation.get("sheet"))
        or bool(citation.get("bbox"))
    )
    if not has_location or not citation.get("evidenceId"):
        raise ValueError("first citation must include evidenceId and a source location")
    retrieval_citations = {
        (item.get("evidenceId"), item.get("documentVersion"))
        for hit in current_hits
        for item in hit.get("citations") or []
    }
    citation_key = (citation.get("evidenceId"), citation.get("documentVersion"))
    if citation_key not in retrieval_citations:
        raise ValueError("websocket citation must match a retrieval citation")
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
    print(f"RHA E2E verified: version={report['pipeline']['documentVersion']} evidence={citations[0]['evidenceId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

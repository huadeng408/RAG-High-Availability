#!/usr/bin/env python3
"""Validate the small deterministic RHA E2E report contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    pipeline = report.get("pipeline") or {}
    answer = report.get("answer") or {}
    if pipeline.get("status") != "SEARCHABLE":
        raise ValueError("pipeline.status must be SEARCHABLE")
    if not pipeline.get("documentVersion"):
        raise ValueError("pipeline.documentVersion is required")
    if pipeline.get("alias") != "rha-knowledge-active":
        raise ValueError("pipeline.alias must point at rha-knowledge-active")
    if not report.get("traceId"):
        raise ValueError("traceId is required")
    citations = answer.get("citations") or []
    if not citations:
        raise ValueError("answer.citations must contain a page-level citation")
    citation = citations[0]
    if int(citation.get("page", 0)) <= 0 or not citation.get("evidenceId"):
        raise ValueError("first citation must include page and evidenceId")
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
    print(f"RHA E2E verified: version={report['pipeline']['documentVersion']} evidence={report['answer']['citations'][0]['evidenceId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

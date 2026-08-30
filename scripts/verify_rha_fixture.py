#!/usr/bin/env python3
"""Validate the deterministic multimodal contract fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "testdata" / "rha_multimodal_fixture.json"
REQUIRED_MODALITIES = {"pdf", "word", "ppt", "excel"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    try:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        documents = fixture["documents"]
        modalities = {document["modality"] for document in documents}
        require(modalities == REQUIRED_MODALITIES, f"expected modalities {sorted(REQUIRED_MODALITIES)}, got {sorted(modalities)}")

        evidence_ids: set[str] = set()
        for document in documents:
            modality = document["modality"]
            for unit in document["evidenceUnits"]:
                evidence_id = unit["evidenceId"]
                require(evidence_id not in evidence_ids, f"duplicate evidence id: {evidence_id}")
                evidence_ids.add(evidence_id)
                require(unit["documentVersion"] == document["documentVersion"], f"wrong version for {evidence_id}")
                if modality == "pdf":
                    require(unit.get("page", 0) > 0 and unit.get("bbox"), f"pdf provenance missing for {evidence_id}")
                elif modality == "word":
                    require(unit.get("headingPath"), f"word heading missing for {evidence_id}")
                elif modality == "ppt":
                    require(unit.get("slide", 0) > 0, f"ppt slide missing for {evidence_id}")
                elif modality == "excel":
                    require(unit.get("sheet") and unit.get("header") and unit.get("rowStart", 0) > 0, f"excel location missing for {evidence_id}")

        require(evidence_ids, "fixture contains no evidence")
        print(f"validated {len(documents)} modalities and {len(evidence_ids)} unique evidence units")
        return 0
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"fixture validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

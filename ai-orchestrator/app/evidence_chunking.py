from __future__ import annotations

from collections.abc import Iterable

from .models import EvidenceUnitPayload, StructuredChunkPayload


def chunks_from_evidence(evidence_units: Iterable[EvidenceUnitPayload]) -> list[StructuredChunkPayload]:
    chunks: list[StructuredChunkPayload] = []
    for unit in evidence_units:
        if not unit.text.strip():
            continue
        chunks.append(
            StructuredChunkPayload(
                id=f"{unit.documentVersion}:{unit.evidenceId}:chunk",
                documentVersion=unit.documentVersion,
                text=unit.text,
                modality=unit.modality,
                headingPath=unit.headingPath,
                page=unit.page,
                slide=unit.slide,
                sheet=unit.sheet,
                rowStart=unit.rowStart,
                rowEnd=unit.rowEnd,
                evidenceIds=[unit.evidenceId],
            )
        )
    return chunks

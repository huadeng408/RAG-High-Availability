"""Structured multimodal parsing with explicit provenance contracts."""

from __future__ import annotations

import json
import re
import subprocess
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.etree import ElementTree

from .models import (
    BoundingBoxPayload,
    EvidenceUnitPayload,
    ParsedDocumentPayload,
    ParserReceiptPayload,
    StructuredChunkPayload,
)


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "testdata" / "rha_multimodal_fixture.json"
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class StructuredParseError(RuntimeError):
    """Raised when a source cannot produce a valid structured artifact."""


class MinerUUnavailableError(StructuredParseError):
    """Raised when MinerU and its OCR receipt are not available."""


def parse_fixture_document(modality: str, document_version: str) -> ParsedDocumentPayload:
    """Load a deterministic parser result; callers must opt in through fixture mode."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    document = next((item for item in fixture["documents"] if item["modality"] == modality), None)
    if document is None:
        raise StructuredParseError(f"fixture modality is not available: {modality}")

    parser = document.get("parser") or {}
    evidence = [
        EvidenceUnitPayload(
            **{
                **item,
                "documentVersion": document_version,
                "parserName": parser.get("name", "fixture"),
                "parserVersion": parser.get("version", "1"),
            }
        )
        for item in document["evidenceUnits"]
    ]
    return ParsedDocumentPayload(
        sourceId=document["sourceId"],
        fileName=document["fileName"],
        documentVersion=document_version,
        modality=modality,
        parserReceipt=ParserReceiptPayload(
            engine="mineru+ocr" if modality == "pdf" else f"fixture-{modality}",
            version=str(parser.get("version", "1")),
            ocrPerformed=modality == "pdf",
        ),
        evidenceUnits=evidence,
        chunks=chunks_from_evidence(evidence),
    )


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


class FixtureParser:
    """Parser adapter used only when RHA_INGESTION_MODE=fixture is explicit."""

    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        del source_path
        raise StructuredParseError("fixture parsing requires a modality")

    def parse_modality(self, modality: str, document_version: str) -> ParsedDocumentPayload:
        return parse_fixture_document(modality, document_version)


class MinerUParser:
    """Runs a configured MinerU command that emits an OCR JSON receipt on stdout."""

    def __init__(self, command: str, timeout_seconds: int = 120) -> None:
        self._command = command.strip()
        self._timeout_seconds = timeout_seconds

    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        if not self._command:
            raise MinerUUnavailableError("MinerU command is not configured")
        try:
            completed = subprocess.run(
                [self._command, str(source_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError as error:
            raise MinerUUnavailableError(f"MinerU command is unavailable: {self._command}") from error
        except subprocess.TimeoutExpired as error:
            raise MinerUUnavailableError("MinerU timed out before producing an OCR receipt") from error

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            raise MinerUUnavailableError(f"MinerU failed: {detail}")
        try:
            receipt = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise MinerUUnavailableError("MinerU did not emit a JSON OCR receipt") from error

        ocr = receipt.get("ocr")
        if not isinstance(ocr, dict) or not ocr.get("enabled"):
            raise MinerUUnavailableError("MinerU receipt does not confirm OCR")

        evidence: list[EvidenceUnitPayload] = []
        for page_data in receipt.get("pages") or []:
            page = int(page_data.get("page", 0))
            for element_index, element in enumerate(page_data.get("elements") or [], start=1):
                text = str(element.get("text", "")).strip()
                bbox = _bounding_box(element.get("bbox"))
                if page <= 0 or not text or bbox is None:
                    continue
                evidence.append(
                    EvidenceUnitPayload(
                        evidenceId=f"{document_version}:pdf:{page}:{element_index}",
                        documentVersion=document_version,
                        modality="pdf",
                        elementType=str(element.get("type", "ocr_text")),
                        page=page,
                        bbox=bbox,
                        text=text,
                        parserName="mineru+ocr",
                        parserVersion=str(receipt.get("version", "")),
                        assetPath=str(source_path),
                    )
                )
        if not evidence:
            raise MinerUUnavailableError("MinerU OCR receipt contains no page evidence with bounding boxes")
        return ParsedDocumentPayload(
            fileName=source_path.name,
            documentVersion=document_version,
            modality="pdf",
            parserReceipt=ParserReceiptPayload(
                engine="mineru+ocr",
                version=str(receipt.get("version", "")),
                ocrPerformed=True,
            ),
            evidenceUnits=evidence,
            chunks=chunks_from_evidence(evidence),
        )


class WordParser:
    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        with zipfile.ZipFile(source_path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        heading_path: list[str] = []
        evidence: list[EvidenceUnitPayload] = []
        for index, paragraph in enumerate(root.findall(f".//{WORD_NS}p"), start=1):
            text = "".join(node.text or "" for node in paragraph.findall(f".//{WORD_NS}t")).strip()
            if not text:
                continue
            style = paragraph.find(f"{WORD_NS}pPr/{WORD_NS}pStyle")
            style_name = style.get(f"{WORD_NS}val", "") if style is not None else ""
            level = _heading_level(style_name)
            if level:
                heading_path = heading_path[: level - 1] + [text]
                continue
            evidence.append(
                EvidenceUnitPayload(
                    evidenceId=f"{document_version}:word:{index}",
                    documentVersion=document_version,
                    modality="word",
                    elementType="paragraph",
                    headingPath=heading_path,
                    text=text,
                    parserName="zip-word",
                    parserVersion="1",
                    assetPath=str(source_path),
                )
            )
        return _parsed_document(source_path, document_version, "word", "zip-word", evidence)


class PptParser:
    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        with zipfile.ZipFile(source_path) as archive:
            slide_names = sorted(
                (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda name: int(re.search(r"\d+", Path(name).stem).group()),
            )
            evidence = []
            for slide, name in enumerate(slide_names, start=1):
                root = ElementTree.fromstring(archive.read(name))
                text = " ".join(node.text or "" for node in root.findall(f".//{DRAWING_NS}t")).strip()
                if text:
                    evidence.append(
                        EvidenceUnitPayload(
                            evidenceId=f"{document_version}:ppt:{slide}",
                            documentVersion=document_version,
                            modality="ppt",
                            elementType="slide_text",
                            slide=slide,
                            text=text,
                            parserName="zip-ppt",
                            parserVersion="1",
                            assetPath=str(source_path),
                        )
                    )
        return _parsed_document(source_path, document_version, "ppt", "zip-ppt", evidence)


class ExcelParser:
    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        with zipfile.ZipFile(source_path) as archive:
            shared_strings = _xlsx_shared_strings(archive)
            worksheets = _xlsx_worksheets(archive)
            evidence: list[EvidenceUnitPayload] = []
            for sheet_name, sheet_path in worksheets:
                root = ElementTree.fromstring(archive.read(sheet_path))
                rows = [_xlsx_row_values(row, shared_strings) for row in root.findall(f".//{SHEET_NS}row")]
                rows = [row for row in rows if any(cell.strip() for cell in row)]
                if len(rows) < 2:
                    continue
                header = rows[0]
                for start in range(1, len(rows), 25):
                    window = rows[start : start + 25]
                    if not window:
                        continue
                    row_start = start + 1
                    row_end = start + len(window)
                    text = "\n".join(" | ".join(row) for row in window)
                    evidence.append(
                        EvidenceUnitPayload(
                            evidenceId=f"{document_version}:excel:{sheet_name}:{row_start}-{row_end}",
                            documentVersion=document_version,
                            modality="excel",
                            elementType="table_window",
                            sheet=sheet_name,
                            rowStart=row_start,
                            rowEnd=row_end,
                            header=header,
                            text=text,
                            parserName="zip-excel",
                            parserVersion="1",
                            assetPath=str(source_path),
                        )
                    )
        return _parsed_document(source_path, document_version, "excel", "zip-excel", evidence)


class PlainTextParser:
    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        text = source_path.read_text(encoding="utf-8", errors="replace").strip()
        evidence = [
            EvidenceUnitPayload(
                evidenceId=f"{document_version}:text:1",
                documentVersion=document_version,
                modality="text",
                elementType="text",
                text=text,
                parserName="plain-text",
                parserVersion="1",
                assetPath=str(source_path),
            )
        ] if text else []
        return _parsed_document(source_path, document_version, "text", "plain-text", evidence)


class ParserRegistry:
    def __init__(self, mineru_command: str, mineru_timeout_seconds: int) -> None:
        self._pdf = MinerUParser(mineru_command, mineru_timeout_seconds)
        self._word = WordParser()
        self._ppt = PptParser()
        self._excel = ExcelParser()
        self._text = PlainTextParser()

    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        suffix = source_path.suffix.lower()
        if suffix == ".pdf":
            return self._pdf.parse(source_path, document_version)
        if suffix == ".docx":
            return self._word.parse(source_path, document_version)
        if suffix == ".pptx":
            return self._ppt.parse(source_path, document_version)
        if suffix == ".xlsx":
            return self._excel.parse(source_path, document_version)
        return self._text.parse(source_path, document_version)


def _parsed_document(
    source_path: Path,
    document_version: str,
    modality: str,
    engine: str,
    evidence: list[EvidenceUnitPayload],
) -> ParsedDocumentPayload:
    if not evidence:
        raise StructuredParseError(f"{modality} parser produced no evidence")
    return ParsedDocumentPayload(
        fileName=source_path.name,
        documentVersion=document_version,
        modality=modality,
        parserReceipt=ParserReceiptPayload(engine=engine, version="1"),
        evidenceUnits=evidence,
        chunks=chunks_from_evidence(evidence),
    )


def _bounding_box(value: object) -> BoundingBoxPayload | None:
    if isinstance(value, dict):
        try:
            return BoundingBoxPayload(**value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, list) and len(value) == 4:
        return BoundingBoxPayload(x0=float(value[0]), y0=float(value[1]), x1=float(value[2]), y1=float(value[3]))
    return None


def _heading_level(style_name: str) -> int:
    match = re.search(r"heading\s*(\d+)$", style_name, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.findall(f".//{SHEET_NS}t")) for item in root.findall(f"{SHEET_NS}si")]


def _xlsx_worksheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relation_root = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relations = {relation.get("Id"): relation.get("Target", "") for relation in relation_root.findall(f"{PACKAGE_REL_NS}Relationship")}
    worksheets: list[tuple[str, str]] = []
    for sheet in root.findall(f".//{SHEET_NS}sheet"):
        relationship_id = sheet.get(f"{REL_NS}id", "")
        target = relations.get(relationship_id, "")
        if target:
            worksheets.append((sheet.get("name", "Sheet"), f"xl/{target.lstrip('/')}"))
    return worksheets


def _xlsx_row_values(row: ElementTree.Element, shared_strings: list[str]) -> list[str]:
    values: list[str] = []
    for cell in row.findall(f"{SHEET_NS}c"):
        cell_type = cell.get("t", "")
        value = cell.findtext(f"{SHEET_NS}v", default="")
        if cell_type == "s" and value.isdigit() and int(value) < len(shared_strings):
            values.append(shared_strings[int(value)])
        elif cell_type == "inlineStr":
            values.append("".join(node.text or "" for node in cell.findall(f".//{SHEET_NS}t")))
        else:
            values.append(value)
    return values

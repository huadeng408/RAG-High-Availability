from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.ingestion import IngestionService
from app.structured_ingestion import MinerUParser, MinerUUnavailableError, ParserRegistry, parse_fixture_document


class StructuredIngestionTests(unittest.TestCase):
    def test_fixture_pdf_keeps_ocr_page_and_bbox_evidence(self) -> None:
        parsed = parse_fixture_document("pdf", "v-pdf")

        self.assertEqual("v-pdf", parsed.documentVersion)
        self.assertEqual("mineru+ocr", parsed.parserReceipt.engine)
        self.assertTrue(parsed.evidenceUnits)
        self.assertTrue(all(unit.page > 0 and unit.bbox is not None for unit in parsed.evidenceUnits))
        self.assertTrue(all(unit.documentVersion == "v-pdf" for unit in parsed.evidenceUnits))

    def test_fixture_office_documents_keep_native_locations(self) -> None:
        word = parse_fixture_document("word", "v-word")
        ppt = parse_fixture_document("ppt", "v-ppt")
        excel = parse_fixture_document("excel", "v-excel")

        self.assertTrue(all(unit.headingPath for unit in word.evidenceUnits))
        self.assertTrue(all(unit.slide > 0 for unit in ppt.evidenceUnits))
        self.assertTrue(all(unit.sheet and unit.header and unit.rowStart > 0 for unit in excel.evidenceUnits))
        self.assertTrue(all(chunk.evidenceIds for chunk in [*word.chunks, *ppt.chunks, *excel.chunks]))

    def test_production_pdf_does_not_fall_back_to_tika(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "receipt.pdf"
            source.write_bytes(b"not-a-real-pdf")
            with self.assertRaises(MinerUUnavailableError):
                MinerUParser(command="missing-rha-mineru").parse(source, "v-pdf")

    def test_service_uses_fixture_only_when_fixture_mode_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "receipt.pdf"
            source.write_bytes(b"fixture-source")

            fixture_service = object.__new__(IngestionService)
            fixture_service._settings = SimpleNamespace(ingestion_mode="fixture")
            fixture_service._parser_registry = ParserRegistry("missing-rha-mineru", 1)
            parsed = fixture_service._parse_source(source, "v-fixture")
            self.assertEqual("mineru+ocr", parsed.parserReceipt.engine)

            production_service = object.__new__(IngestionService)
            production_service._settings = SimpleNamespace(ingestion_mode="production")
            production_service._parser_registry = ParserRegistry("missing-rha-mineru", 1)
            with self.assertRaises(MinerUUnavailableError):
                production_service._parse_source(source, "v-production")


if __name__ == "__main__":
    unittest.main()

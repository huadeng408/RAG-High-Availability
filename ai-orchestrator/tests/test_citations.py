from __future__ import annotations

import json
import asyncio
import unittest

import httpx
from langchain_core.documents import Document

from app import main as orchestrator_main
from app.graph import _build_citations
from app.models import BoundingBoxPayload, CitationPayload, SearchResultPayload, build_done_event
from app.retrievers import search_result_to_document


class CitationStreamTests(unittest.TestCase):
    def test_search_result_provenance_survives_document_conversion(self) -> None:
        result = SearchResultPayload(
            fileMd5="file-md5",
            fileName="invoice.pdf",
            chunkId=2,
            textContent="The invoice amount is 1200.",
            citations=[
                CitationPayload(
                    evidenceId="e-invoice-page-2",
                    documentVersion="v-invoice",
                    modality="pdf",
                    page=2,
                    bbox=BoundingBoxPayload(x0=12, y0=16, x1=220, y1=48),
                    sourcePath="uploads/invoice.pdf",
                )
            ],
        )

        document = search_result_to_document(result, "hybrid")
        citations = _build_citations([document])

        self.assertEqual(["e-invoice-page-2"], [citation.evidenceId for citation in citations])
        self.assertEqual(2, citations[0].page)
        self.assertEqual("uploads/invoice.pdf", citations[0].sourcePath)

    def test_done_event_contains_deduplicated_page_citations(self) -> None:
        docs = [
            Document(
                page_content="The invoice amount is 1200.",
                metadata={
                    "evidenceId": "e-invoice-page-2",
                    "documentVersion": "v-invoice",
                    "modality": "pdf",
                    "page": 2,
                    "bbox": {"x0": 12.0, "y0": 16.0, "x1": 220.0, "y1": 48.0},
                    "label": "invoice.pdf page 2",
                    "sourcePath": "uploads/invoice.pdf",
                },
            ),
            Document(
                page_content="The invoice amount is 1200.",
                metadata={
                    "evidenceId": "e-invoice-page-2",
                    "documentVersion": "v-invoice",
                    "modality": "pdf",
                    "page": 2,
                    "bbox": {"x0": 12.0, "y0": 16.0, "x1": 220.0, "y1": 48.0},
                    "label": "invoice.pdf page 2",
                    "sourcePath": "uploads/invoice.pdf",
                },
            ),
        ]

        event = build_done_event("trace-citation-1", _build_citations(docs))

        self.assertEqual("done", event.type)
        self.assertTrue(event.done)
        self.assertEqual("trace-citation-1", event.traceId)
        self.assertEqual(["e-invoice-page-2"], [citation.evidenceId for citation in event.citations])
        self.assertEqual(2, event.citations[0].page)
        self.assertIsNotNone(event.citations[0].bbox)
        self.assertEqual(12.0, event.citations[0].bbox.x0)


class ChatStreamEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().set_debug(False)

    async def test_chat_stream_emits_trace_and_deduplicated_citations(self) -> None:
        citation = CitationPayload(
            evidenceId="e-invoice-page-2",
            documentVersion="v-invoice",
            modality="pdf",
            page=2,
            bbox=BoundingBoxPayload(x0=12, y0=16, x1=220, y1=48),
            sourcePath="uploads/invoice.pdf",
        )

        class StubGraph:
            async def ainvoke(self, state):
                state["stream_callback"]("The amount is 1200.")
                return {"citations": [citation, citation]}

        original_graph = orchestrator_main.graph
        original_token = orchestrator_main.settings.internal_token
        orchestrator_main.graph = StubGraph()
        orchestrator_main.settings.internal_token = "test-internal-token"
        try:
            transport = httpx.ASGITransport(app=orchestrator_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/chat/stream",
                    headers={"X-Internal-Token": "test-internal-token", "X-Trace-ID": "trace-http-citation"},
                    json={"query": "Where is the amount?", "user": {"id": 7}},
                )
        finally:
            orchestrator_main.graph = original_graph
            orchestrator_main.settings.internal_token = original_token

        self.assertEqual(200, response.status_code)
        events = [json.loads(line) for line in response.text.splitlines() if line]
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual("trace-http-citation", done["traceId"])
        self.assertEqual(["e-invoice-page-2"], [item["evidenceId"] for item in done["citations"]])
        self.assertEqual(2, done["citations"][0]["page"])
        self.assertEqual(12.0, done["citations"][0]["bbox"]["x0"])


if __name__ == "__main__":
    unittest.main()

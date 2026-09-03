from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.documents import Document

from app.config import ModelSettings, Settings
from app.graph import build_graph


class GraphContractTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def settings() -> Settings:
        return Settings(
            host="127.0.0.1", port=8090, go_base_url="http://go", internal_token="token",
            request_timeout_seconds=1,
            llm=ModelSettings("http://llm", "", "stub", 0.0, 0.0, 1),
            planner=ModelSettings("http://llm", "", "stub", 0.0, 0.0, 1),
            embedding=ModelSettings("http://embed", "", "stub", 0.0, 0.0, 0),
            embedding_dimensions=8, ingestion_mode="fixture", mineru_command="mineru",
            mineru_timeout_seconds=1, image_allowed_mime_types=("image/png",), image_max_bytes=1,
            image_max_pixels=1, image_ocr_url="", image_ocr_timeout_seconds=1,
            image_allow_textless=False, image_vlm_enabled=False, image_vlm_timeout_seconds=1,
            image_vlm=ModelSettings("http://vlm", "", "stub", 0.0, 0.0, 1),
            es_url="http://es", es_username="", es_password="", knowledge_top_k=1,
            memory_top_k=1, context_top_k=1, rrf_k=1, debug_traces=False,
        )

    def test_graph_contains_exact_eleven_linear_nodes(self) -> None:
        with patch("app.graph._build_model", return_value=object()):
            graph = build_graph(self.settings(), object())
        expected = {
            "load_history", "classify_intent", "rewrite_query", "prepare_prompt_context",
            "retrieve_knowledge", "retrieve_memory", "fuse_context", "rerank_context",
            "build_messages", "generate_answer", "persist_memory",
        }
        self.assertEqual(expected, set(graph.get_graph().nodes) - {"__start__", "__end__"})
        ordered = [
            "__start__", "load_history", "classify_intent", "rewrite_query", "prepare_prompt_context",
            "retrieve_knowledge", "retrieve_memory", "fuse_context", "rerank_context",
            "build_messages", "generate_answer", "persist_memory", "__end__",
        ]
        self.assertEqual(
            set(zip(ordered, ordered[1:])),
            {(edge.source, edge.target) for edge in graph.get_graph().edges},
        )

    async def test_reranker_timeout_keeps_fused_documents_and_marks_degradation(self) -> None:
        class TimeoutBackend:
            async def rerank_context(self, _payload):
                raise TimeoutError("reranker timeout")

        with patch("app.graph._build_model", return_value=object()):
            graph = build_graph(self.settings(), TimeoutBackend())
        rerank_node = graph.get_graph().nodes["rerank_context"].data
        fused = Document(
            page_content="lexical fallback",
            metadata={"id": "fallback-1", "sourceType": "knowledge", "label": "fallback"},
        )

        result = await rerank_node.ainvoke({"query": "fallback", "context_top_k": 1, "fused_docs": [fused]})

        self.assertEqual([fused], result["reranked_docs"])
        self.assertEqual("lexical fallback", result["context_items"][0]["text"])
        self.assertTrue(result["rerank_skipped"])
        self.assertTrue(result["rerank_timed_out"])


if __name__ == "__main__":
    unittest.main()

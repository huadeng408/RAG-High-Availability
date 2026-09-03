from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODEL_STUB_PATH = ROOT / "deployments" / "e2e" / "model_stub.py"


def load_model_stub():
    spec = importlib.util.spec_from_file_location("rha_model_stub", MODEL_STUB_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    fixture = {"engine": "test-ocr", "version": "1", "regions": []}
    original_read_text = Path.read_text

    def read_text(path: Path, *args, **kwargs) -> str:
        if path.as_posix().endswith("/app/rha_image_fixture.json"):
            return json.dumps(fixture)
        return original_read_text(path, *args, **kwargs)

    with patch.object(Path, "read_text", read_text):
        spec.loader.exec_module(module)
    return module


class RhaModelStubTest(unittest.TestCase):
    def test_embedding_failure_can_be_enabled_and_cleared_for_recovery_e2e(self) -> None:
        model_stub = load_model_stub()
        request = model_stub.EmbeddingRequest(input=["recovery evidence"], dimensions=8)

        state = model_stub.set_failures(model_stub.FailureControl(embeddings=True))
        self.assertTrue(state["embeddings"])
        with self.assertRaises(model_stub.HTTPException) as raised:
            model_stub.embeddings(request)
        self.assertEqual(raised.exception.status_code, 503)

        state = model_stub.set_failures(model_stub.FailureControl(embeddings=False))
        self.assertFalse(state["embeddings"])
        response = model_stub.embeddings(request)
        self.assertEqual(len(response["data"]), 1)

    def test_reranker_delay_and_failure_controls_are_bounded_and_readable(self) -> None:
        model_stub = load_model_stub()
        state = model_stub.set_failures(model_stub.FailureControl(reranker_delay_ms=25))
        self.assertEqual(25, state["reranker_delay_ms"])
        state = model_stub.set_failures(model_stub.FailureControl(reranker=True))
        self.assertTrue(state["reranker"])
        with self.assertRaises(model_stub.HTTPException) as raised:
            model_stub.rerank(model_stub.RerankRequest(query="q", documents=["d"]))
        self.assertEqual(503, raised.exception.status_code)
        state = model_stub.set_failures(model_stub.FailureControl())
        self.assertFalse(state["reranker"])

    def test_chat_answers_the_image_fact_when_context_contains_ocr_result(self) -> None:
        model_stub = load_model_stub()
        request = model_stub.ChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": "Use this evidence: RHA image inspection code is IMG-2048.",
                }
            ]
        )

        response = model_stub.chat(request)

        self.assertIn("IMG-2048", response["choices"][0]["message"]["content"])

    def test_chat_does_not_select_image_answer_from_unrelated_system_context(self) -> None:
        model_stub = load_model_stub()
        request = model_stub.ChatRequest(
            messages=[
                {"role": "system", "content": "Available evidence includes IMG-2048."},
                {"role": "user", "content": "What is the RHA retention period?"},
            ]
        )

        response = model_stub.chat(request)

        answer = response["choices"][0]["message"]["content"]
        self.assertNotIn("IMG-2048", answer)
        self.assertIn("七年", answer)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.memory_tasks import MemoryTaskService
from app.models import ChatMessagePayload, MemorySummaryRequestPayload, MemoryWriteRequestPayload


class _PlainTextModel:
    async def ainvoke(self, _messages):
        return SimpleNamespace(content="依据 RHA fixture：保留期限为七年。")


class MemoryTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_summarize_degrades_when_model_ignores_json_contract(self) -> None:
        service = object.__new__(MemoryTaskService)
        service._model = _PlainTextModel()

        result = await service.summarize(
            MemorySummaryRequestPayload(
                history=[ChatMessagePayload(role="user", content="请记住 RHA 项目")],
            )
        )

        self.assertEqual("", result.summary)
        self.assertEqual([], result.facts)
        self.assertEqual([], result.profile_updates)

    async def test_extract_degrades_when_model_ignores_json_contract(self) -> None:
        service = object.__new__(MemoryTaskService)
        service._model = _PlainTextModel()

        result = await service.extract(
            MemoryWriteRequestPayload(
                question="保留期限是多少？",
                answer="依据 RHA fixture：保留期限为七年。",
            )
        )

        self.assertFalse(result.should_store)
        self.assertEqual("fact", result.memory_type)
        self.assertEqual([], result.profile_updates)


if __name__ == "__main__":
    unittest.main()

"""Regression tests for the deterministic OpenAI-compatible model stub."""

from __future__ import annotations

import asyncio

from fastapi.responses import StreamingResponse

from model_stub import ChatRequest, chat


async def _read_stream(response: StreamingResponse) -> str:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    return b"".join(chunks).decode("utf-8")


def test_streaming_chat_returns_openai_sse_events() -> None:
    response = chat(ChatRequest(messages=[{"role": "user", "content": "hello"}], stream=True))

    assert isinstance(response, StreamingResponse)
    body = asyncio.run(_read_stream(response))
    assert "data: " in body
    assert '"choices"' in body
    assert body.rstrip().endswith("data: [DONE]")

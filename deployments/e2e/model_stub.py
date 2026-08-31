"""Deterministic OpenAI-compatible model, embedding, and rerank stub for E2E."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

app = FastAPI(title="RHA deterministic model stub")


class EmbeddingRequest(BaseModel):
    input: list[str] | str = Field(default_factory=list)
    model: str = "rha-fixture-embedding"
    dimensions: int = 8


class RerankRequest(BaseModel):
    query: str
    documents: list[str] = Field(default_factory=list)
    top_n: int = 5
    model: str = "rha-fixture-reranker"


class ChatRequest(BaseModel):
    model: str = "rha-fixture-chat"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False


def _vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round((digest[idx % len(digest)] / 255.0) * 2 - 1, 6) for idx in range(max(1, dimensions))]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/embeddings")
def embeddings(payload: EmbeddingRequest) -> dict[str, Any]:
    texts = [payload.input] if isinstance(payload.input, str) else payload.input
    return {
        "object": "list",
        "model": payload.model,
        "data": [{"object": "embedding", "index": idx, "embedding": _vector(text, payload.dimensions)} for idx, text in enumerate(texts)],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/rerank")
def rerank(payload: RerankRequest) -> dict[str, Any]:
    ranked = sorted(
        ({"index": idx, "relevance_score": 1.0 if payload.query.strip() and payload.query in text else 0.5} for idx, text in enumerate(payload.documents)),
        key=lambda item: item["relevance_score"],
        reverse=True,
    )
    return {"model": payload.model, "latency_ms": 0.0, "results": ranked[: payload.top_n]}


@app.post("/v1/chat/completions", response_model=None)
def chat(payload: ChatRequest) -> dict[str, Any] | StreamingResponse:
    answer = "依据 RHA fixture：保留期限为七年。"
    if payload.stream:
        return StreamingResponse(_stream_chat_events(answer), media_type="text/event-stream")

    return {
        "id": "rha-fixture-chat-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
    }


def _stream_chat_events(answer: str):
    pieces = [answer[:8], answer[8:14], answer[14:]]
    for index, piece in enumerate(pieces):
        delta: dict[str, str] = {"content": piece}
        if index == 0:
            delta["role"] = "assistant"
        event = {
            "id": "rha-fixture-chat-stream-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "rha-fixture-chat",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    finish_event = {
        "id": "rha-fixture-chat-stream-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "rha-fixture-chat",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(finish_event, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"

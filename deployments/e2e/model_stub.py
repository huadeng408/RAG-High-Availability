"""Deterministic OpenAI-compatible model, embedding, and rerank stub for E2E."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

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


@app.post("/v1/chat/completions")
def chat(payload: ChatRequest) -> dict[str, Any]:
    return {
        "id": "rha-fixture-chat-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "依据 RHA fixture：保留期限为七年。"}, "finish_reason": "stop"}],
    }

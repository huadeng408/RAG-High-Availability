"""Deterministic OpenAI-compatible model, embedding, and rerank stub for E2E."""

from __future__ import annotations

import hashlib
import base64
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
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


class ImageOCRRequest(BaseModel):
    imageBase64: str
    mimeType: str
    width: int
    height: int
    assetSha256: str


class FailureControl(BaseModel):
    embeddings: bool = False


IMAGE_OCR_FIXTURE = json.loads(Path("/app/rha_image_fixture.json").read_text(encoding="utf-8"))
FAILURES = {"embeddings": False}


def _vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [round((digest[idx % len(digest)] / 255.0) * 2 - 1, 6) for idx in range(max(1, dimensions))]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.put("/control/failures")
def set_failures(payload: FailureControl) -> dict[str, bool]:
    FAILURES["embeddings"] = payload.embeddings
    return dict(FAILURES)


@app.post("/embeddings")
def embeddings(payload: EmbeddingRequest) -> dict[str, Any]:
    if FAILURES["embeddings"]:
        raise HTTPException(status_code=503, detail="E2E embedding failure enabled")
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


@app.post("/image/ocr")
def image_ocr(payload: ImageOCRRequest) -> dict[str, Any]:
    try:
        contents = base64.b64decode(payload.imageBase64, validate=True)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="invalid image base64") from error
    if hashlib.sha256(contents).hexdigest() != payload.assetSha256:
        raise HTTPException(status_code=400, detail="asset hash mismatch")
    if payload.mimeType != "image/png" or not contents.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=415, detail="E2E OCR accepts normalized PNG input")
    if (payload.width, payload.height) != (320, 120):
        raise HTTPException(status_code=422, detail="unexpected E2E image dimensions")
    return IMAGE_OCR_FIXTURE


@app.post("/v1/chat/completions", response_model=None)
def chat(payload: ChatRequest) -> dict[str, Any] | StreamingResponse:
    last_user_content: Any = ""
    for message in reversed(payload.messages):
        if str(message.get("role", "")).lower() == "user":
            last_user_content = message.get("content", "")
            break
    query = json.dumps(last_user_content, ensure_ascii=False).lower()
    answer = (
        "依据 RHA 图片证据：巡检编码为 IMG-2048。"
        if "image inspection code" in query or "巡检编码" in query
        else "依据 RHA fixture：保留期限为七年。"
    )
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

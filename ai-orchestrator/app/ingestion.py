from __future__ import annotations

import json
import mimetypes
import time

import httpx
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import Settings
from .models import (
    ChunkRequestPayload,
    ChunkResponsePayload,
    EmbedRequestPayload,
    EmbedResponsePayload,
    IndexRequestPayload,
    IndexResponsePayload,
    ParseRequestPayload,
    ParseResponsePayload,
)
from .trace import current_trace_id, elapsed_ms, log_request


class IngestionService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=settings.request_timeout_seconds, trust_env=False)
        embedding_kwargs: dict[str, object] = {
            "model": settings.embedding.model,
            "api_key": settings.embedding.api_key or "not-needed",
            "base_url": settings.embedding.base_url,
            "request_timeout": settings.request_timeout_seconds,
            "max_retries": 2,
        }
        if settings.embedding_dimensions > 0:
            embedding_kwargs["dimensions"] = settings.embedding_dimensions
        self._embeddings = OpenAIEmbeddings(**embedding_kwargs)

    async def close(self) -> None:
        await self._http.aclose()

    async def parse(self, payload: ParseRequestPayload) -> ParseResponsePayload:
        start = time.perf_counter()
        source_resp = await self._http.get(payload.objectUrl)
        source_resp.raise_for_status()

        tika_resp = await self._http.put(
            f"{self._settings.tika_url.rstrip('/')}/tika",
            content=source_resp.content,
            headers={
                "Accept": "text/plain",
                "Content-Type": _detect_mime_type(payload.task.file_name),
            },
        )
        tika_resp.raise_for_status()
        parsed_text = tika_resp.text.strip()
        log_request("ingestion_parse", latency_ms=elapsed_ms(start), file_md5=payload.task.file_md5)
        return ParseResponsePayload(parsedText=parsed_text)

    async def chunk(self, payload: ChunkRequestPayload) -> ChunkResponsePayload:
        start = time.perf_counter()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=max(1, payload.chunkSize),
            chunk_overlap=max(0, min(payload.chunkOverlap, payload.chunkSize - 1 if payload.chunkSize > 1 else 0)),
            separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
        )
        documents = splitter.split_documents(
            [
                Document(
                    page_content=payload.text,
                    metadata={
                        "file_md5": payload.task.file_md5,
                        "file_name": payload.task.file_name,
                    },
                )
            ]
        )
        chunks = [doc.page_content for doc in documents if doc.page_content.strip()]
        log_request("ingestion_chunk", latency_ms=elapsed_ms(start), file_md5=payload.task.file_md5, chunks=len(chunks))
        return ChunkResponsePayload(chunks=chunks)

    async def embed(self, payload: EmbedRequestPayload) -> EmbedResponsePayload:
        start = time.perf_counter()
        if not payload.texts:
            return EmbedResponsePayload(vectors=[])
        vectors = await self._embeddings.aembed_documents(payload.texts)
        log_request("ingestion_embed", latency_ms=elapsed_ms(start), file_md5=payload.task.file_md5, batch=len(payload.texts))
        return EmbedResponsePayload(vectors=[[float(value) for value in item] for item in vectors])

    async def index(self, payload: IndexRequestPayload) -> IndexResponsePayload:
        start = time.perf_counter()
        if not payload.docs:
            return IndexResponsePayload(indexedCount=0)

        bulk_lines: list[str] = []
        for doc in payload.docs:
            vector_id = str(doc.get("vector_id", "")).strip()
            meta = {"index": {"_index": payload.indexName}}
            if vector_id:
                meta["index"]["_id"] = vector_id
            bulk_lines.append(json.dumps(meta, ensure_ascii=False))
            bulk_lines.append(json.dumps(doc, ensure_ascii=False))
        bulk_body = "\n".join(bulk_lines) + "\n"

        headers = {"Content-Type": "application/x-ndjson"}
        auth = None
        if self._settings.es_username:
            auth = (self._settings.es_username, self._settings.es_password)
        headers["X-Trace-ID"] = current_trace_id()

        response = await self._http.post(
            f"{self._settings.es_url.rstrip('/')}/_bulk?refresh=true",
            content=bulk_body.encode("utf-8"),
            headers=headers,
            auth=auth,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError("bulk index completed with partial failures")
        items = body.get("items") or []
        log_request("ingestion_index", latency_ms=elapsed_ms(start), file_md5=payload.task.file_md5, indexed=len(items))
        return IndexResponsePayload(indexedCount=len(items))


def _detect_mime_type(file_name: str) -> str:
    mime_type, _ = mimetypes.guess_type(file_name)
    return mime_type or "application/octet-stream"

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class UserPayload(BaseModel):
    id: int
    username: str = ""
    role: str = "USER"
    orgTags: str = ""
    primaryOrg: str = ""


class ChatMessagePayload(BaseModel):
    role: str
    content: str
    timestamp: datetime | None = None


class BoundingBoxPayload(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class ImageMetadataPayload(BaseModel):
    assetSha256: str
    mimeType: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    orientationNormalized: bool = False
    ocrConfidence: float | None = Field(default=None, ge=0, le=1)
    visionModel: str = ""


class CitationPayload(BaseModel):
    evidenceId: str
    label: str = ""
    documentVersion: str = ""
    modality: str = ""
    page: int = 0
    slide: int = 0
    sheet: str = ""
    bbox: BoundingBoxPayload | None = None
    image: ImageMetadataPayload | None = None
    excerpt: str = ""
    sourcePath: str = ""


class ContextSnippetPayload(BaseModel):
    id: str = ""
    sourceType: str = ""
    label: str = ""
    text: str = ""
    score: float = 0.0
    timestamp: datetime | None = None
    citations: list[CitationPayload] = Field(default_factory=list)


class FileProcessingTaskPayload(BaseModel):
    file_md5: str
    document_version: str = ""
    window_id: str = ""
    trace_id: str = ""
    object_url: str = ""
    file_name: str
    user_id: int
    org_tag: str = ""
    is_public: bool = False
    stage: str
    task_chunk_id: int = 0
    chunk_start: int = 0
    total_chunks: int = 0
    parsed_object: str = ""
    last_error: str = ""


class ChatStreamRequest(BaseModel):
    query: str = Field(min_length=1)
    user: UserPayload


class SessionResponse(BaseModel):
    conversationId: str
    history: list[ChatMessagePayload] = Field(default_factory=list)


class RetrievePlanPayload(BaseModel):
    intent: str = "single_hop"
    rewritten_query: str = ""
    sub_queries: list[str] = Field(default_factory=list)
    retrieval_mode: str = "hybrid"
    enable_rerank: bool | None = None
    need_history: bool = False
    skip_retrieval: bool = False
    reason: str = ""


class RetrieveRequestPayload(BaseModel):
    user: UserPayload
    query: str
    conversationId: str = ""
    history: list[ChatMessagePayload] = Field(default_factory=list)
    plan: RetrievePlanPayload


class RetrieveResponse(BaseModel):
    conversationId: str
    history: list[ChatMessagePayload] = Field(default_factory=list)
    sensoryHistory: list[ChatMessagePayload] = Field(default_factory=list)
    memoryPrelude: str = ""
    knowledgeItems: list[ContextSnippetPayload] = Field(default_factory=list)
    memoryItems: list[ContextSnippetPayload] = Field(default_factory=list)
    contextItems: list[ContextSnippetPayload] = Field(default_factory=list)
    contextText: str = ""
    systemMessage: str = ""


class PromptContextRequestPayload(BaseModel):
    user: UserPayload
    conversationId: str = ""
    history: list[ChatMessagePayload] = Field(default_factory=list)
    plan: RetrievePlanPayload


class PromptContextResponse(BaseModel):
    conversationId: str
    history: list[ChatMessagePayload] = Field(default_factory=list)
    sensoryHistory: list[ChatMessagePayload] = Field(default_factory=list)
    memoryPrelude: str = ""
    promptRules: str = ""
    refStart: str = "<<REF>>"
    refEnd: str = "<<END>>"
    noResultText: str = "(no retrieval result in this turn)"
    knowledgeTopK: int = 8
    contextTopK: int = 6
    rrfK: int = 60


class SearchResultPayload(BaseModel):
    fileMd5: str = ""
    fileName: str = ""
    chunkId: int = 0
    textContent: str = ""
    score: float = 0.0
    userId: str = ""
    orgTag: str = ""
    isPublic: bool = False
    citations: list[CitationPayload] = Field(default_factory=list)


class KnowledgeSearchRequestPayload(BaseModel):
    user: UserPayload
    query: str
    topK: int = 8
    disableRerank: bool = True
    mode: Literal["hybrid", "bm25", "vector"] = "hybrid"


class KnowledgeSearchResponse(BaseModel):
    results: list[SearchResultPayload] = Field(default_factory=list)


class MemorySearchRequestPayload(BaseModel):
    user: UserPayload
    query: str
    history: list[ChatMessagePayload] = Field(default_factory=list)
    plan: RetrievePlanPayload


class MemorySearchResponse(BaseModel):
    items: list[ContextSnippetPayload] = Field(default_factory=list)


class RerankContextRequestPayload(BaseModel):
    query: str
    topK: int = 6
    items: list[ContextSnippetPayload] = Field(default_factory=list)


class RerankContextResponse(BaseModel):
    items: list[ContextSnippetPayload] = Field(default_factory=list)


class ProfileUpdatePayload(BaseModel):
    slot_key: str = ""
    slot_value: str = ""
    confidence: float = 0.0


class MemorySummaryRequestPayload(BaseModel):
    history: list[ChatMessagePayload] = Field(default_factory=list)
    workingHistoryMessages: int = 12
    workingMaxFacts: int = 6


class MemorySummaryResponsePayload(BaseModel):
    summary: str = ""
    facts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    profile_updates: list[ProfileUpdatePayload] = Field(default_factory=list)


class MemoryWriteRequestPayload(BaseModel):
    question: str
    answer: str
    workingSummary: str = ""


class MemoryWriteResponsePayload(BaseModel):
    should_store: bool = False
    memory_type: str = "fact"
    summary: str = ""
    content: str = ""
    entities: list[str] = Field(default_factory=list)
    importance: float = 0.0
    profile_updates: list[ProfileUpdatePayload] = Field(default_factory=list)


class ParseRequestPayload(BaseModel):
    task: FileProcessingTaskPayload
    objectUrl: str


class ParserReceiptPayload(BaseModel):
    engine: str
    version: str = ""
    ocrPerformed: bool = False


class EvidenceUnitPayload(BaseModel):
    evidenceId: str
    documentVersion: str
    modality: str
    elementType: str
    page: int = 0
    slide: int = 0
    sheet: str = ""
    rowStart: int = 0
    rowEnd: int = 0
    headingPath: list[str] = Field(default_factory=list)
    header: list[str] = Field(default_factory=list)
    bbox: BoundingBoxPayload | None = None
    image: ImageMetadataPayload | None = None
    text: str
    parserName: str = ""
    parserVersion: str = ""
    assetPath: str = ""


class StructuredChunkPayload(BaseModel):
    id: str
    documentVersion: str
    text: str
    modality: str
    headingPath: list[str] = Field(default_factory=list)
    page: int = 0
    slide: int = 0
    sheet: str = ""
    rowStart: int = 0
    rowEnd: int = 0
    evidenceIds: list[str] = Field(default_factory=list)


class ParsedDocumentPayload(BaseModel):
    sourceId: str = ""
    fileName: str = ""
    documentVersion: str
    modality: str
    parserReceipt: ParserReceiptPayload
    evidenceUnits: list[EvidenceUnitPayload] = Field(default_factory=list)
    chunks: list[StructuredChunkPayload] = Field(default_factory=list)


class ParseResponsePayload(BaseModel):
    parsedDocument: ParsedDocumentPayload


class ChunkRequestPayload(BaseModel):
    task: FileProcessingTaskPayload
    parsedDocument: ParsedDocumentPayload
    chunkSize: int = 500
    chunkOverlap: int = 50


class ChunkResponsePayload(BaseModel):
    chunks: list[StructuredChunkPayload] = Field(default_factory=list)


class EmbedRequestPayload(BaseModel):
    task: FileProcessingTaskPayload
    texts: list[str] = Field(default_factory=list)


class EmbedResponsePayload(BaseModel):
    vectors: list[list[float]] = Field(default_factory=list)


class IndexRequestPayload(BaseModel):
    task: FileProcessingTaskPayload
    indexName: str
    docs: list[dict[str, Any]] = Field(default_factory=list)


class IndexResponsePayload(BaseModel):
    indexedCount: int = 0


class PersistRequestPayload(BaseModel):
    user: UserPayload
    conversationId: str = ""
    history: list[ChatMessagePayload] = Field(default_factory=list)
    query: str
    answer: str


class PlannerDecision(BaseModel):
    intent: Literal["single_hop", "follow_up", "comparison", "troubleshooting", "chitchat"] = "single_hop"
    rewritten_query: str = ""
    sub_queries: list[str] = Field(default_factory=list)
    retrieval_mode: Literal["hybrid", "bm25", "vector"] = "hybrid"
    enable_rerank: bool | None = None
    need_history: bool = False
    skip_retrieval: bool = False
    reason: str = ""


class StreamEvent(BaseModel):
    type: Literal["chunk", "trace", "error", "done"] = "chunk"
    chunk: str = ""
    trace: str = ""
    error: str = ""
    done: bool = False
    traceId: str = ""
    citations: list[CitationPayload] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_done_event(
    trace_id: str,
    citations: list[CitationPayload],
    metadata: dict[str, Any] | None = None,
) -> StreamEvent:
    seen_evidence: set[str] = set()
    unique_citations: list[CitationPayload] = []
    for citation in citations:
        if not citation.evidenceId or citation.evidenceId in seen_evidence:
            continue
        seen_evidence.add(citation.evidenceId)
        unique_citations.append(citation)
    return StreamEvent(
        type="done",
        done=True,
        traceId=trace_id,
        citations=unique_citations,
        metadata=metadata or {},
    )

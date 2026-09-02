from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(slots=True)
class ModelSettings:
    base_url: str
    api_key: str
    model: str
    temperature: float
    top_p: float
    max_tokens: int


@dataclass(slots=True)
class Settings:
    host: str
    port: int
    go_base_url: str
    internal_token: str
    request_timeout_seconds: int
    llm: ModelSettings
    planner: ModelSettings
    embedding: ModelSettings
    embedding_dimensions: int
    ingestion_mode: str
    mineru_command: str
    mineru_timeout_seconds: int
    image_allowed_mime_types: tuple[str, ...]
    image_max_bytes: int
    image_max_pixels: int
    image_ocr_url: str
    image_ocr_timeout_seconds: int
    image_allow_textless: bool
    image_vlm_enabled: bool
    image_vlm_timeout_seconds: int
    image_vlm: ModelSettings
    es_url: str
    es_username: str
    es_password: str
    knowledge_top_k: int
    memory_top_k: int
    context_top_k: int
    rrf_k: int
    debug_traces: bool


def load_settings() -> Settings:
    llm = ModelSettings(
        base_url=os.getenv("RHA_LLM_BASE_URL", "https://api.deepseek.com/v1").strip(),
        api_key=os.getenv("RHA_LLM_API_KEY", "").strip(),
        model=os.getenv("RHA_LLM_MODEL", "deepseek-chat").strip(),
        temperature=_get_float("RHA_LLM_TEMPERATURE", 0.3),
        top_p=_get_float("RHA_LLM_TOP_P", 0.9),
        max_tokens=_get_int("RHA_LLM_MAX_TOKENS", 2000),
    )
    planner = ModelSettings(
        base_url=os.getenv("RHA_PLANNER_BASE_URL", llm.base_url).strip(),
        api_key=os.getenv("RHA_PLANNER_API_KEY", llm.api_key).strip(),
        model=os.getenv("RHA_PLANNER_MODEL", llm.model).strip(),
        temperature=_get_float("RHA_PLANNER_TEMPERATURE", 0.1),
        top_p=_get_float("RHA_PLANNER_TOP_P", llm.top_p),
        max_tokens=_get_int("RHA_PLANNER_MAX_TOKENS", 256),
    )
    embedding = ModelSettings(
        base_url=os.getenv("RHA_EMBEDDING_BASE_URL", llm.base_url).strip(),
        api_key=os.getenv("RHA_EMBEDDING_API_KEY", llm.api_key).strip(),
        model=os.getenv("RHA_EMBEDDING_MODEL", "").strip(),
        temperature=0.0,
        top_p=0.0,
        max_tokens=0,
    )
    image_vlm = ModelSettings(
        base_url=os.getenv("RHA_IMAGE_VLM_BASE_URL", llm.base_url).strip(),
        api_key=os.getenv("RHA_IMAGE_VLM_API_KEY", llm.api_key).strip(),
        model=os.getenv("RHA_IMAGE_VLM_MODEL", "").strip(),
        temperature=0.0,
        top_p=1.0,
        max_tokens=_get_int("RHA_IMAGE_VLM_MAX_TOKENS", 384),
    )
    return Settings(
        host=os.getenv("RHA_HOST", "0.0.0.0").strip(),
        port=_get_int("RHA_PORT", 8090),
        go_base_url=os.getenv("RHA_GO_BASE_URL", "http://127.0.0.1:8081").strip(),
        internal_token=os.getenv("RHA_INTERNAL_TOKEN", "").strip(),
        request_timeout_seconds=_get_int("RHA_REQUEST_TIMEOUT_SECONDS", 120),
        llm=llm,
        planner=planner,
        embedding=embedding,
        embedding_dimensions=_get_int("RHA_EMBEDDING_DIMENSIONS", 512),
        ingestion_mode=os.getenv("RHA_INGESTION_MODE", "production").strip().lower(),
        mineru_command=os.getenv("RHA_MINERU_COMMAND", "mineru").strip(),
        mineru_timeout_seconds=_get_int("RHA_MINERU_TIMEOUT_SECONDS", 120),
        image_allowed_mime_types=tuple(
            item.strip().lower()
            for item in os.getenv("RHA_IMAGE_ALLOWED_MIME_TYPES", "image/png,image/jpeg").split(",")
            if item.strip()
        ),
        image_max_bytes=_get_int("RHA_IMAGE_MAX_BYTES", 20 * 1024 * 1024),
        image_max_pixels=_get_int("RHA_IMAGE_MAX_PIXELS", 40_000_000),
        image_ocr_url=os.getenv("RHA_IMAGE_OCR_URL", "").strip(),
        image_ocr_timeout_seconds=_get_int("RHA_IMAGE_OCR_TIMEOUT_SECONDS", 30),
        image_allow_textless=_get_bool("RHA_IMAGE_ALLOW_TEXTLESS", False),
        image_vlm_enabled=_get_bool("RHA_IMAGE_VLM_ENABLED", False),
        image_vlm_timeout_seconds=_get_int("RHA_IMAGE_VLM_TIMEOUT_SECONDS", 45),
        image_vlm=image_vlm,
        es_url=os.getenv("RHA_ES_URL", "http://127.0.0.1:9200").strip(),
        es_username=os.getenv("RHA_ES_USERNAME", "").strip(),
        es_password=os.getenv("RHA_ES_PASSWORD", "").strip(),
        knowledge_top_k=_get_int("RHA_RETRIEVAL_KNOWLEDGE_TOP_K", 8),
        memory_top_k=_get_int("RHA_RETRIEVAL_MEMORY_TOP_K", 4),
        context_top_k=_get_int("RHA_RETRIEVAL_CONTEXT_TOP_K", 6),
        rrf_k=_get_int("RHA_RETRIEVAL_RRF_K", 60),
        debug_traces=_get_bool("RHA_DEBUG_TRACES", False),
    )

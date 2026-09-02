"""Validated image parsing and replaceable OCR/VLM adapters."""

from __future__ import annotations

import base64
import hashlib
import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .evidence_chunking import chunks_from_evidence
from .models import (
    BoundingBoxPayload,
    EvidenceUnitPayload,
    ImageMetadataPayload,
    ParsedDocumentPayload,
    ParserReceiptPayload,
)


FORMAT_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg"}
SUFFIX_MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class ImageParseError(RuntimeError):
    """Raised when an image cannot produce policy-compliant evidence."""


@dataclass(frozen=True, slots=True)
class OCRTextRegion:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class OCRResult:
    engine: str
    version: str
    regions: list[OCRTextRegion]


class ImageOCRAdapter(Protocol):
    def extract(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        width: int,
        height: int,
        asset_sha256: str,
    ) -> OCRResult: ...


class ImageVLMAdapter(Protocol):
    model_name: str

    def summarize(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        width: int,
        height: int,
        asset_sha256: str,
    ) -> str: ...


class HTTPImageOCRAdapter:
    """Calls an OCR service using a small JSON receipt contract."""

    def __init__(self, endpoint: str, timeout_seconds: float = 30) -> None:
        self._endpoint = endpoint.strip()
        self._timeout_seconds = timeout_seconds
        if not self._endpoint:
            raise ValueError("image OCR endpoint is required")

    def extract(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        width: int,
        height: int,
        asset_sha256: str,
    ) -> OCRResult:
        payload = {
            "imageBase64": base64.b64encode(image_bytes).decode("ascii"),
            "mimeType": mime_type,
            "width": width,
            "height": height,
            "assetSha256": asset_sha256,
        }
        with httpx.Client(timeout=self._timeout_seconds, trust_env=False) as client:
            response = client.post(self._endpoint, json=payload)
            response.raise_for_status()
            receipt = response.json()
        if not isinstance(receipt, dict):
            raise ImageParseError("OCR service returned a non-object receipt")
        regions: list[OCRTextRegion] = []
        for item in receipt.get("regions") or []:
            if not isinstance(item, dict):
                raise ImageParseError("OCR service returned an invalid region")
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            confidence = item.get("confidence")
            parsed_confidence = None if confidence is None else float(confidence)
            if parsed_confidence is not None and not 0 <= parsed_confidence <= 1:
                raise ImageParseError("OCR confidence must be between zero and one")
            regions.append(
                OCRTextRegion(
                    text=text,
                    bbox=_coerce_bbox(item.get("bbox")),
                    confidence=parsed_confidence,
                )
            )
        return OCRResult(
            engine=str(receipt.get("engine", "http-ocr")).strip() or "http-ocr",
            version=str(receipt.get("version", "")).strip(),
            regions=regions,
        )


class OpenAICompatibleVLMAdapter:
    """Uses an OpenAI-compatible chat-completions endpoint for image summaries."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 45,
        max_tokens: int = 384,
    ) -> None:
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key.strip()
        self.model_name = model.strip()
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        if not base_url.strip() or not self.model_name:
            raise ValueError("VLM base URL and model are required")

    def summarize(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        width: int,
        height: int,
        asset_sha256: str,
    ) -> str:
        del width, height, asset_sha256
        image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        payload = {
            "model": self.model_name,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the operational facts visible in this image. Do not infer unreadable text."},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        with httpx.Client(timeout=self._timeout_seconds, trust_env=False) as client:
            response = client.post(self._endpoint, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ImageParseError("VLM response did not contain message content") from error
        if isinstance(content, list):
            content = " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return str(content).strip()


class ImageParser:
    def __init__(
        self,
        *,
        ocr: ImageOCRAdapter | None,
        vlm: ImageVLMAdapter | None = None,
        max_bytes: int = 20 * 1024 * 1024,
        max_pixels: int = 40_000_000,
        allowed_mime_types: tuple[str, ...] = ("image/png", "image/jpeg"),
        allow_textless: bool = False,
    ) -> None:
        if max_bytes <= 0 or max_pixels <= 0:
            raise ValueError("image byte and pixel limits must be positive")
        self._ocr = ocr
        self._vlm = vlm
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels
        self._allowed_mime_types = frozenset(item.strip().lower() for item in allowed_mime_types if item.strip())
        self._allow_textless = allow_textless

    def parse(self, source_path: Path, document_version: str) -> ParsedDocumentPayload:
        if source_path.stat().st_size > self._max_bytes:
            raise ImageParseError(f"image exceeds byte limit of {self._max_bytes}")
        normalized_bytes, mime_type, width, height, orientation_normalized = self._decode_image(source_path)
        asset_sha256 = hashlib.sha256(normalized_bytes).hexdigest()

        ocr_result = None
        if self._ocr is not None:
            ocr_result = self._ocr.extract(
                image_bytes=normalized_bytes,
                mime_type=mime_type,
                width=width,
                height=height,
                asset_sha256=asset_sha256,
            )
        vlm_summary = ""
        if self._vlm is not None:
            vlm_summary = self._vlm.summarize(
                image_bytes=normalized_bytes,
                mime_type=mime_type,
                width=width,
                height=height,
                asset_sha256=asset_sha256,
            ).strip()

        evidence: list[EvidenceUnitPayload] = []
        if ocr_result is not None:
            for index, region in enumerate(ocr_result.regions, start=1):
                bbox = _validated_bbox(region.bbox, width=width, height=height)
                evidence.append(
                    EvidenceUnitPayload(
                        evidenceId=f"{document_version}:image:ocr:{index}",
                        documentVersion=document_version,
                        modality="image",
                        elementType="ocr_text",
                        bbox=bbox,
                        image=ImageMetadataPayload(
                            assetSha256=asset_sha256,
                            mimeType=mime_type,
                            width=width,
                            height=height,
                            orientationNormalized=orientation_normalized,
                            ocrConfidence=region.confidence,
                        ),
                        text=region.text,
                        parserName=ocr_result.engine,
                        parserVersion=ocr_result.version,
                        assetPath=str(source_path),
                    )
                )
        if vlm_summary:
            evidence.append(
                EvidenceUnitPayload(
                    evidenceId=f"{document_version}:image:vlm:1",
                    documentVersion=document_version,
                    modality="image",
                    elementType="vlm_summary",
                    bbox=BoundingBoxPayload(x0=0, y0=0, x1=width, y1=height),
                    image=ImageMetadataPayload(
                        assetSha256=asset_sha256,
                        mimeType=mime_type,
                        width=width,
                        height=height,
                        orientationNormalized=orientation_normalized,
                        visionModel=self._vlm.model_name if self._vlm is not None else "",
                    ),
                    text=vlm_summary,
                    parserName="openai-compatible-vlm",
                    parserVersion=self._vlm.model_name if self._vlm is not None else "",
                    assetPath=str(source_path),
                )
            )
        if not evidence:
            if not self._allow_textless:
                raise ImageParseError("image produced no OCR text or VLM summary")
            evidence.append(
                EvidenceUnitPayload(
                    evidenceId=f"{document_version}:image:asset:1",
                    documentVersion=document_version,
                    modality="image",
                    elementType="image_asset",
                    bbox=BoundingBoxPayload(x0=0, y0=0, x1=width, y1=height),
                    image=ImageMetadataPayload(
                        assetSha256=asset_sha256,
                        mimeType=mime_type,
                        width=width,
                        height=height,
                        orientationNormalized=orientation_normalized,
                    ),
                    text="Image asset; OCR and VLM produced no text.",
                    parserName="pillow-image",
                    parserVersion=Image.__version__,
                    assetPath=str(source_path),
                )
            )

        engines: list[str] = []
        if ocr_result is not None:
            engines.append(ocr_result.engine)
        if self._vlm is not None:
            engines.append("vlm:" + self._vlm.model_name)
        if not engines:
            engines.append("pillow-image")
        return ParsedDocumentPayload(
            fileName=source_path.name,
            documentVersion=document_version,
            modality="image",
            parserReceipt=ParserReceiptPayload(
                engine="+".join(engines),
                version="1",
                ocrPerformed=ocr_result is not None,
            ),
            evidenceUnits=evidence,
            chunks=chunks_from_evidence(evidence),
        )

    def _decode_image(self, source_path: Path) -> tuple[bytes, str, int, int, bool]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source_path) as source:
                    detected_mime = FORMAT_MIME_TYPES.get(str(source.format).upper())
                    if detected_mime not in self._allowed_mime_types:
                        raise ImageParseError(f"unsupported image MIME: {detected_mime or source.format or 'unknown'}")
                    expected_mime = SUFFIX_MIME_TYPES.get(source_path.suffix.lower())
                    if expected_mime != detected_mime:
                        raise ImageParseError(
                            f"image extension/MIME mismatch: expected {expected_mime or 'unsupported'}, detected {detected_mime}"
                        )
                    width, height = source.size
                    if width <= 0 or height <= 0 or width * height > self._max_pixels:
                        raise ImageParseError(f"image exceeds pixel limit of {self._max_pixels}")
                    orientation = int(source.getexif().get(274, 1) or 1)
                    normalized = ImageOps.exif_transpose(source)
                    normalized.load()
                    orientation_normalized = orientation not in (0, 1)
                    width, height = normalized.size
                    output = io.BytesIO()
                    if detected_mime == "image/jpeg":
                        normalized.convert("RGB").save(
                            output,
                            format="JPEG",
                            quality=95,
                            optimize=False,
                            progressive=False,
                            subsampling=0,
                        )
                    else:
                        if normalized.mode not in ("RGB", "RGBA"):
                            normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
                        normalized.save(output, format="PNG", optimize=False, compress_level=9)
                    return output.getvalue(), detected_mime, width, height, orientation_normalized
        except ImageParseError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ImageParseError("image exceeds decoder safety limits") from error
        except (UnidentifiedImageError, OSError, ValueError) as error:
            raise ImageParseError("invalid or corrupted image") from error


def _coerce_bbox(value: object) -> tuple[float, float, float, float]:
    if isinstance(value, dict):
        values = [value.get("x0"), value.get("y0"), value.get("x1"), value.get("y1")]
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        values = list(value)
    else:
        raise ImageParseError("OCR region bbox must contain four coordinates")
    try:
        return tuple(float(item) for item in values)  # type: ignore[return-value]
    except (TypeError, ValueError) as error:
        raise ImageParseError("OCR region bbox contains a non-numeric coordinate") from error


def _validated_bbox(
    value: tuple[float, float, float, float],
    *,
    width: int,
    height: int,
) -> BoundingBoxPayload:
    x0, y0, x1, y1 = value
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ImageParseError(f"OCR region bbox is outside the {width}x{height} image")
    return BoundingBoxPayload(x0=x0, y0=y0, x1=x1, y1=y1)

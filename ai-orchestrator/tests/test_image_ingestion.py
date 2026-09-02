from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from app.image_ingestion import (
    HTTPImageOCRAdapter,
    ImageParseError,
    ImageParser,
    OCRResult,
    OCRTextRegion,
    OpenAICompatibleVLMAdapter,
)


class StubOCR:
    def __init__(self, regions: list[OCRTextRegion]) -> None:
        self.regions = regions
        self.calls: list[dict[str, object]] = []

    def extract(self, **kwargs) -> OCRResult:
        self.calls.append(kwargs)
        return OCRResult(engine="stub-ocr", version="2026.09", regions=self.regions)


class StubVLM:
    model_name = "stub-vision"

    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.calls: list[dict[str, object]] = []

    def summarize(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.summary


def save_image(path: Path, *, size: tuple[int, int], image_format: str, orientation: int = 1) -> None:
    image = Image.new("RGB", size, color=(240, 240, 240))
    if image_format == "JPEG":
        exif = Image.Exif()
        exif[274] = orientation
        image.save(path, format=image_format, exif=exif)
    else:
        image.save(path, format=image_format)


class ImageParserTests(unittest.TestCase):
    def test_png_ocr_region_keeps_pixel_location_and_asset_identity(self) -> None:
        ocr = StubOCR([OCRTextRegion(text="Gauge P-204 reads 10 bar", bbox=(2, 3, 30, 18), confidence=0.97)])
        parser = ImageParser(ocr=ocr, max_bytes=1024 * 1024, max_pixels=100_000)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gauge.png"
            save_image(source, size=(64, 32), image_format="PNG")
            parsed = parser.parse(source, "version-image-1")

        self.assertEqual("image", parsed.modality)
        self.assertTrue(parsed.parserReceipt.ocrPerformed)
        self.assertEqual(1, len(parsed.evidenceUnits))
        evidence = parsed.evidenceUnits[0]
        self.assertEqual("version-image-1:image:ocr:1", evidence.evidenceId)
        self.assertEqual((2.0, 3.0, 30.0, 18.0), (evidence.bbox.x0, evidence.bbox.y0, evidence.bbox.x1, evidence.bbox.y1))
        self.assertEqual(64, evidence.image.width)
        self.assertEqual(32, evidence.image.height)
        self.assertEqual("image/png", evidence.image.mimeType)
        self.assertEqual(64, len(evidence.image.assetSha256))
        self.assertAlmostEqual(0.97, evidence.image.ocrConfidence)
        self.assertEqual([evidence.evidenceId], parsed.chunks[0].evidenceIds)

    def test_jpeg_exif_orientation_is_normalized_before_ocr(self) -> None:
        ocr = StubOCR([OCRTextRegion(text="rotated label", bbox=(0, 0, 3, 2), confidence=0.8)])
        parser = ImageParser(ocr=ocr, max_bytes=1024 * 1024, max_pixels=100_000)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "label.jpg"
            save_image(source, size=(2, 3), image_format="JPEG", orientation=6)
            parsed = parser.parse(source, "version-image-2")

        call = ocr.calls[0]
        self.assertEqual((3, 2), (call["width"], call["height"]))
        self.assertEqual("image/jpeg", call["mime_type"])
        self.assertTrue(parsed.evidenceUnits[0].image.orientationNormalized)

    def test_rejects_unsupported_format_and_resource_limits(self) -> None:
        parser = ImageParser(ocr=StubOCR([]), max_bytes=1024 * 1024, max_pixels=100)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gif = root / "unsupported.gif"
            save_image(gif, size=(2, 2), image_format="GIF")
            with self.assertRaisesRegex(ImageParseError, "unsupported image MIME"):
                parser.parse(gif, "v-gif")

            large = root / "large.png"
            save_image(large, size=(11, 10), image_format="PNG")
            with self.assertRaisesRegex(ImageParseError, "pixel limit"):
                parser.parse(large, "v-large")

            byte_heavy = root / "byte-heavy.png"
            save_image(byte_heavy, size=(8, 8), image_format="PNG")
            with self.assertRaisesRegex(ImageParseError, "byte limit"):
                ImageParser(ocr=StubOCR([]), max_bytes=8, max_pixels=100).parse(byte_heavy, "v-bytes")

    def test_vlm_summary_is_versioned_full_image_evidence(self) -> None:
        vlm = StubVLM("Pressure gauge P-204 reads 10 bar.")
        parser = ImageParser(ocr=None, vlm=vlm, max_bytes=1024 * 1024, max_pixels=100_000)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gauge.png"
            save_image(source, size=(40, 20), image_format="PNG")
            parsed = parser.parse(source, "version-image-vlm")

        self.assertFalse(parsed.parserReceipt.ocrPerformed)
        evidence = parsed.evidenceUnits[0]
        self.assertEqual("vlm_summary", evidence.elementType)
        self.assertEqual("stub-vision", evidence.image.visionModel)
        self.assertEqual((0.0, 0.0, 40.0, 20.0), (evidence.bbox.x0, evidence.bbox.y0, evidence.bbox.x1, evidence.bbox.y1))

    def test_textless_policy_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blank.png"
            save_image(source, size=(12, 8), image_format="PNG")
            with self.assertRaisesRegex(ImageParseError, "no OCR text or VLM summary"):
                ImageParser(ocr=StubOCR([])).parse(source, "v-blank")

            parsed = ImageParser(ocr=StubOCR([]), allow_textless=True).parse(source, "v-blank")

        self.assertEqual("image_asset", parsed.evidenceUnits[0].elementType)
        self.assertTrue(parsed.parserReceipt.ocrPerformed)
        self.assertTrue(parsed.chunks)


class ImageAdapterTests(unittest.TestCase):
    def test_http_ocr_and_openai_compatible_vlm_contracts(self) -> None:
        observed: dict[str, dict] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed[self.path] = payload
                if self.path == "/ocr":
                    response = {
                        "engine": "paddleocr-service",
                        "version": "3.0",
                        "regions": [{"text": "valve A17", "bbox": [1, 2, 20, 12], "confidence": 0.91}],
                    }
                else:
                    response = {"choices": [{"message": {"content": "Valve A17 is closed."}}]}
                encoded = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            image_bytes = b"normalized-image"
            ocr = HTTPImageOCRAdapter(base_url + "/ocr", timeout_seconds=2).extract(
                image_bytes=image_bytes,
                mime_type="image/png",
                width=32,
                height=16,
                asset_sha256="a" * 64,
            )
            summary = OpenAICompatibleVLMAdapter(
                base_url=base_url + "/v1",
                api_key="runtime-only-key",
                model="vision-test",
                timeout_seconds=2,
            ).summarize(
                image_bytes=image_bytes,
                mime_type="image/png",
                width=32,
                height=16,
                asset_sha256="a" * 64,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual("valve A17", ocr.regions[0].text)
        self.assertEqual("Valve A17 is closed.", summary)
        self.assertEqual("a" * 64, observed["/ocr"]["assetSha256"])
        image_url = observed["/v1/chat/completions"]["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()

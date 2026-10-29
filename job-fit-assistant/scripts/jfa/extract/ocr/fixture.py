# -*- coding: utf-8 -*-
"""Test double for the OCR pipeline. NOT a recogniser.

It replays a JSON sidecar that a test authored by hand:

    <image>.ocr.json  ->  {"lines": [{"text": "...", "confidence": 0.9,
                                      "bbox": [0,0,10,10]}], "model": "..."}

Two guards stop this from ever passing as real OCR:

* it is only importable/usable with ``JFA_ALLOW_TEST_PROVIDER=1``;
* every block it produces is tagged ``fixture_test_double`` + ``unverified``,
  and the extraction report carries a loud warning.

This exists so the review/correction workflow can be tested deterministically
on a machine where PaddleOCR is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...errors import OcrUnavailable
from .base import OcrLine, OcrProvider, OcrResult


class FixtureProvider(OcrProvider):
    name = "fixture"
    supports_structure = False

    @classmethod
    def available(cls) -> bool:
        from .base import fixture_enabled

        return fixture_enabled()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "available": self.available(),
            "version": "test-double",
            "runtime": "test-only",
            "note": "回放人工编写的 JSON 旁车文件，仅用于测试；不是真实 OCR。",
            "install": "无需安装；生产环境请使用 paddleocr。",
        }

    def recognize(self, image_path: Path, *, page: int | None = None,
                  lang: str = "ch", structure: bool = False) -> OcrResult:
        if not self.available():
            raise OcrUnavailable(
                "fixture 提供者仅供测试，需要显式设置 JFA_ALLOW_TEST_PROVIDER=1。",
                hint="生产环境请安装 PaddleOCR；fixture 不会产生真实识别结果。",
            )
        sidecar = Path(str(image_path) + ".ocr.json")
        if not sidecar.exists():
            raise OcrUnavailable(
                f"缺少测试旁车文件：{sidecar.name}",
                hint="测试夹具需要为每张图片提供 .ocr.json。",
            )
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        result = OcrResult(provider=self.name, model=data.get("model", "fixture"),
                           page=page)
        for row in data.get("lines", []):
            result.lines.append(OcrLine(
                text=str(row.get("text", "")),
                confidence=row.get("confidence"),
                bbox=row.get("bbox"),
                kind=row.get("kind", "text"),
            ))
        result.warnings.append(
            "本次文字来自测试旁车文件（fixture_test_double），不是真实 OCR 结果。"
        )
        return result

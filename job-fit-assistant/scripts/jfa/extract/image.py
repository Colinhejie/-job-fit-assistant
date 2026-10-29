# -*- coding: utf-8 -*-
"""Standalone image extraction (PNG/JPG screenshot of a resume).

There is no text layer to fall back on, so without OCR this raises. Returning an
empty result here would be indistinguishable from "the page was blank".
"""

from __future__ import annotations

from pathlib import Path

from .. import blocks as B
from ..errors import OcrUnavailable
from . import SourceResult
from .ocr.base import OcrProvider


def extract(
    path: Path,
    *,
    source_id: str,
    provider: OcrProvider | None = None,
    lang: str = "ch",
    structure: bool = False,
) -> SourceResult:
    result = SourceResult(source_id=source_id, path=path, kind="image")
    if provider is None:
        raise OcrUnavailable(
            "图片简历必须经过 OCR，但当前没有可用的 OCR 后端。",
            hint="安装本地 PaddleOCR 后重试；或请用户提供 DOCX / 文字型 PDF / 可复制的文本。",
            detail={"file": str(path)},
        )
    try:
        ocr = provider.recognize(path, page=1, lang=lang, structure=structure)
    except OcrUnavailable as exc:
        result.errors.append(exc.message)
        raise

    builder = B.BlockBuilder(source_id)
    for line in ocr.lines:
        builder_made = builder.add(
            line.text,
            method=B.M_FIXTURE if ocr.provider == "fixture" else B.M_OCR,
            kind=B.KIND_OCR_LINE if line.kind in {"text", "ocr_line"} else B.KIND_OCR_LAYOUT,
            page=1,
            locator={"bbox": line.bbox, "layout_type": line.kind,
                             "raw_text": line.raw_text},
            confidence=line.confidence,
        )
        result.blocks.append(builder_made)

    result.report = {
        "provider": ocr.provider,
        "model": ocr.model,
        "lines": len(ocr.lines),
        "has_pages": False,
        "page_note": "单张图片没有页码，统一记为第 1 页（图片本身即一页）。",
        "ocr_used": True,
        "page_images": {1: str(path)},
        "ocr_warnings": ocr.warnings,
    }
    if ocr.warnings:
        result.warnings.extend(ocr.warnings)
    if not result.blocks:
        result.errors.append("OCR 没有识别到任何文字；请确认图片清晰度或人工录入。")
    return result

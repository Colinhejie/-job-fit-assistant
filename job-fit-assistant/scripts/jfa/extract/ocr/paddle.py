# -*- coding: utf-8 -*-
"""PaddleOCR 3.x adapter (local, CPU capable).

Verified against the documented 3.x API:

* `paddleocr.PaddleOCR(...).predict(path)` → results exposing `rec_texts`,
  `rec_scores`, `rec_polys` (PP-OCRv5 pipeline).
* `paddleocr.PPStructureV3(...).predict(path)` → layout blocks for complex
  layouts (PP-StructureV3).

Nothing here runs unless PaddlePaddle and PaddleOCR are importable; otherwise
`available()` is False and the caller reports the capability as missing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ...errors import OcrUnavailable
from ...textnorm import fold_with_raw
from .base import INSTALL_HINT, OcrLine, OcrProvider, OcrResult


class PaddleProvider(OcrProvider):
    name = "paddleocr"
    supports_structure = True

    def __init__(self, *, lang: str = "ch", structure: bool = False):
        self.lang = lang
        self.structure = structure
        self._ocr = None
        self._struct = None

    # ------------------------------------------------------------------
    @classmethod
    def available(cls) -> bool:
        return (importlib.util.find_spec("paddleocr") is not None
                and importlib.util.find_spec("paddle") is not None)

    def describe(self) -> dict:
        version = ""
        if self.available():
            import paddleocr  # noqa: F401

            version = str(getattr(paddleocr, "__version__", ""))
        return {
            "name": self.name,
            "available": self.available(),
            "version": version,
            "lang": self.lang,
            "engine": "PP-OCRv5",
            "structure_engine": "PP-StructureV3" if self.supports_structure else "",
            "runtime": "local",
            "device": "cpu-or-gpu (auto)",
            "network": "首次运行下载模型，之后离线",
            "install": INSTALL_HINT,
        }

    # ------------------------------------------------------------------
    def _ensure(self):
        if not self.available():
            raise OcrUnavailable(
                "PaddleOCR 未安装，无法识别扫描件或图片。",
                hint=INSTALL_HINT,
                detail={"paddleocr": importlib.util.find_spec("paddleocr") is not None,
                        "paddle": importlib.util.find_spec("paddle") is not None},
            )
        if self._ocr is None:
            from paddleocr import PaddleOCR

            try:
                self._ocr = PaddleOCR(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    lang=self.lang,
                )
            except TypeError:
                # older signature
                self._ocr = PaddleOCR(use_angle_cls=False, lang=self.lang, show_log=False)
        return self._ocr

    def _ensure_structure(self):
        if not self.available():
            raise OcrUnavailable("PP-StructureV3 需要 PaddleOCR。", hint=INSTALL_HINT)
        if self._struct is None:
            from paddleocr import PPStructureV3

            try:
                self._struct = PPStructureV3(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                )
            except TypeError:
                self._struct = PPStructureV3()
        return self._struct

    # ------------------------------------------------------------------
    def recognize(self, image_path: Path, *, page: int | None = None,
                  lang: str = "ch", structure: bool = False) -> OcrResult:
        if not Path(image_path).exists():
            raise OcrUnavailable(f"待识别图片不存在：{image_path}")
        if structure and self.supports_structure:
            return self._recognize_structure(image_path, page=page)
        return self._recognize_ocr(image_path, page=page)

    def _recognize_ocr(self, image_path: Path, *, page: int | None) -> OcrResult:
        engine = self._ensure()
        result = OcrResult(provider=self.name, model="PP-OCRv5", page=page)
        try:
            predictions = engine.predict(str(image_path))
        except Exception as exc:  # pragma: no cover - backend dependent
            raise OcrUnavailable(
                f"PaddleOCR 识别失败：{exc}",
                hint="确认模型已下载、内存充足，或改用文字型 PDF/原始 DOCX。",
            ) from exc

        for res in predictions:
            data = _as_dict(res)
            texts = list(data.get("rec_texts") or [])
            scores = list(data.get("rec_scores") or [])
            polys = list(data.get("rec_polys") or [])
            for i, text in enumerate(texts):
                if not text:
                    continue
                bbox = _bbox(polys[i]) if i < len(polys) else None
                conf = float(scores[i]) if i < len(scores) else None
                folded, raw = fold_with_raw(str(text))
                result.lines.append(OcrLine(text=folded, confidence=conf, bbox=bbox,
                                            raw_text=raw))
            if not texts:
                result.warnings.append("该图未识别到文字（可能为空白页或图片分辨率过低）")
        if not result.lines:
            result.warnings.append("OCR 结果为空；不要据此认为页面无内容，请人工核对原图")
        return result

    def _recognize_structure(self, image_path: Path, *, page: int | None) -> OcrResult:
        engine = self._ensure_structure()
        result = OcrResult(provider=self.name, model="PP-StructureV3", page=page)
        try:
            predictions = engine.predict(str(image_path))
        except Exception as exc:  # pragma: no cover
            raise OcrUnavailable(
                f"PP-StructureV3 识别失败：{exc}",
                hint="复杂版式需要更多内存；可改用 structure=false 走 PP-OCRv5。",
            ) from exc

        for res in predictions:
            data = _as_dict(res)
            blocks = data.get("parsing_res_list") or data.get("layout_det_res") or []
            for block in blocks:
                item = _as_dict(block)
                text = str(item.get("block_content") or item.get("text") or "").strip()
                if not text:
                    continue
                bbox = _bbox(item.get("block_bbox") or item.get("bbox"))
                label = str(item.get("block_label") or item.get("label") or "text")
                result.lines.append(OcrLine(text=text, confidence=None, bbox=bbox, kind=label))
                result.layout.append({"type": label, "text": text, "bbox": bbox})
            # PP-StructureV3 也可能带 rec_texts
            for i, text in enumerate(data.get("rec_texts") or []):
                if not text:
                    continue
                scores = data.get("rec_scores") or []
                result.lines.append(OcrLine(
                    text=str(text),
                    confidence=float(scores[i]) if i < len(scores) else None,
                    kind="text",
                ))
        if not result.lines:
            result.warnings.append("结构化识别结果为空，请人工核对原图")
        return result


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    for attr in ("json", "to_dict"):
        value = getattr(obj, attr, None)
        if callable(value):
            try:
                data = value()
                if isinstance(data, dict):
                    return data
                if isinstance(data, str):
                    import json

                    return json.loads(data)
            except Exception:
                continue
    data = getattr(obj, "res", None)
    if isinstance(data, dict):
        return data
    return {}


def _bbox(poly: Any) -> list[float] | None:
    try:
        pts = list(poly)
        if pts and isinstance(pts[0], (list, tuple)):
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
        else:
            xs = [float(pts[0]), float(pts[2])]
            ys = [float(pts[1]), float(pts[3])]
        return [min(xs), min(ys), max(xs), max(ys)]
    except Exception:
        return None

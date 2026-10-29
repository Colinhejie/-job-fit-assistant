# -*- coding: utf-8 -*-
"""RapidOCR adapter (local ONNX, CPU, no PaddlePaddle needed).

Why this backend exists: PaddleOCR is a ~500MB install and its wheels lag new
Python versions. RapidOCR is the same PP-OCR lineage packaged for onnxruntime,
which installs in seconds on Python 3.11/3.12. It gives text + boxes + scores,
but no layout/structure model (use PaddleOCR's PP-StructureV3 for that).

Verified API (rapidocr-onnxruntime 1.4.x):

    from rapidocr_onnxruntime import RapidOCR
    result, elapse = RapidOCR()(image_path)
    # result: [ [box(4 points), text, score], ... ]  or None
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ...errors import OcrUnavailable
from ...textnorm import fold_with_raw
from .base import OcrLine, OcrProvider, OcrResult

INSTALL_HINT = (
    "安装本地 OCR（无需 Paddle，Python 3.11/3.12 最稳）：\n"
    "  pip install rapidocr-onnxruntime\n"
    "首次运行会下载 PP-OCR 的 onnx 模型（约 15MB），之后离线可用。\n"
    "需要版面结构（双栏/表格）时改用 paddleocr 的 PP-StructureV3。"
)


class RapidOcrProvider(OcrProvider):
    name = "rapidocr"
    supports_structure = False

    def __init__(self, *, lang: str = "ch"):
        self.lang = lang
        self._engine = None

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None

    def describe(self) -> dict:
        version = ""
        if self.available():
            try:
                import rapidocr_onnxruntime as r

                version = str(getattr(r, "__version__", "") or "")
            except Exception:
                version = ""
        return {
            "name": self.name,
            "available": self.available(),
            "version": version or "1.4.x",
            "lang": self.lang,
            "engine": "PP-OCR (onnx)",
            "structure_engine": "",
            "runtime": "local",
            "device": "cpu",
            "network": "首次运行下载模型，之后离线",
            "install": INSTALL_HINT,
        }

    def _ensure(self):
        if not self.available():
            raise OcrUnavailable(
                "RapidOCR 未安装，无法识别扫描件或图片。",
                hint=INSTALL_HINT,
            )
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            except Exception as exc:  # pragma: no cover - backend dependent
                raise OcrUnavailable(
                    f"RapidOCR 初始化失败：{exc}",
                    hint=INSTALL_HINT,
                ) from exc
        return self._engine

    def recognize(self, image_path: Path, *, page: int | None = None,
                  lang: str = "ch", structure: bool = False) -> OcrResult:
        if not Path(image_path).exists():
            raise OcrUnavailable(f"待识别图片不存在：{image_path}")
        engine = self._ensure()
        result = OcrResult(provider=self.name, model="PP-OCR(onnx)", page=page)
        try:
            output = engine(str(image_path))
        except Exception as exc:  # pragma: no cover
            raise OcrUnavailable(
                f"RapidOCR 识别失败：{exc}",
                hint="确认图片可读、内存充足；必要时降低 dpi 重试。",
            ) from exc

        rows = _rows(output)
        for row in rows:
            box, text, score = row
            text = str(text or "").strip()
            if not text:
                continue
            folded, raw = fold_with_raw(text)
            result.lines.append(OcrLine(
                text=folded,
                confidence=float(score) if score is not None else None,
                bbox=_bbox(box),
                kind="text",
                raw_text=raw,
            ))
        if not result.lines:
            result.warnings.append("OCR 结果为空；不要据此认为页面无内容，请人工核对原图")
        if structure:
            result.warnings.append(
                "RapidOCR 不含版面结构分析；复杂版式请改用 paddleocr 的 PP-StructureV3"
            )
        return result


def _rows(output: Any) -> list[tuple[Any, Any, Any]]:
    """Normalise `(result, elapse)` across 1.x return shapes."""
    if not output:
        return []
    result = output[0] if isinstance(output, tuple) and len(output) == 2 else output
    if result is None:
        return []
    rows: list[tuple[Any, Any, Any]] = []
    # 1.4.x: list of [box, text, score]
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                rows.append((item[0], item[1], item[2]))
            elif isinstance(item, dict):
                rows.append((item.get("box") or item.get("boxes"),
                             item.get("text") or item.get("txt"),
                             item.get("score")))
        return rows
    # newer object style: .txts / .scores / .boxes
    txts = getattr(result, "txts", None)
    if txts:
        scores = getattr(result, "scores", None) or []
        boxes = getattr(result, "boxes", None) or []
        for i, text in enumerate(txts):
            rows.append((boxes[i] if i < len(boxes) else None, text,
                         scores[i] if i < len(scores) else None))
    return rows


def _bbox(box: Any) -> list[float] | None:
    try:
        pts = list(box)
        if pts and isinstance(pts[0], (list, tuple)):
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
        elif len(pts) >= 4:
            xs = [float(pts[0]), float(pts[2])]
            ys = [float(pts[1]), float(pts[3])]
        else:
            return None
        return [min(xs), min(ys), max(xs), max(ys)]
    except Exception:
        return None

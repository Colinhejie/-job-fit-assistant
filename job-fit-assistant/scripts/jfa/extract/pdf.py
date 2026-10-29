# -*- coding: utf-8 -*-
"""PDF extraction with per-page OCR decisions.

A page is OCR'd only when its own text layer is unusable. The decision is
recorded per page (`use_ocr`, `reason`, `char_count`) so the user can audit it —
"we OCR'd everything just in case" is not acceptable, and neither is pretending a
scanned page produced text.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import blocks as B
from ..errors import DependencyMissing, OcrUnavailable
from . import SourceResult
from .ocr.base import OcrProvider

MIN_TEXT_CHARS = 20
_WEIRD = re.compile(r"[\ufffd\u25a1\u25a0\u0000-\u0008\u000b\u000c]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")

MIN_INK_COVERAGE = 0.0005


def open_pdf(path: Path):
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:
            raise DependencyMissing(
                "未安装 PyMuPDF，无法解析 PDF。",
                hint="pip install -r scripts/requirements.txt",
            ) from exc
    return pymupdf.open(str(path))


def needs_ocr(text: str, *, image_count: int, page_area: float) -> tuple[bool, str]:
    """Decide from the page's own text layer. Returns (need, human reason)."""
    stripped = (text or "").strip()
    meaningful = len(re.sub(r"\s", "", stripped))
    if meaningful == 0:
        return True, "该页没有文字层（纯扫描或纯图片页）"
    if meaningful < MIN_TEXT_CHARS:
        if image_count:
            return True, f"文字层仅 {meaningful} 字且含图片，疑似扫描页"
        return True, f"文字层仅 {meaningful} 字，信息不足"
    weird = len(_WEIRD.findall(stripped))
    if weird / max(len(stripped), 1) > 0.02:
        return True, f"文字层含 {weird} 个不可显示字符，疑似字体子集编码损坏"
    letters = len(_CJK.findall(stripped)) + len(_LATIN.findall(stripped))
    if letters / max(meaningful, 1) < 0.35:
        return True, "文字层中字母/汉字占比过低，疑似符号化乱码"
    return False, "文字层可用，未启用 OCR"


def extract(
    path: Path,
    *,
    source_id: str,
    page_image_dir: Path | None = None,
    provider: OcrProvider | None = None,
    lang: str = "ch",
    dpi: int = 220,
    max_pages: int = 20,
    structure: bool = False,
) -> SourceResult:
    result = SourceResult(source_id=source_id, path=path, kind="pdf")
    doc = open_pdf(path)
    builder = B.BlockBuilder(source_id)
    blocks: list[B.Block] = []
    page_reports: list[dict] = []
    page_images: dict[int, str] = {}
    ocr_failures: list[dict] = []

    if page_image_dir:
        page_image_dir.mkdir(parents=True, exist_ok=True)

    total = doc.page_count
    limit = min(total, max_pages) if max_pages else total
    if total > limit:
        result.warnings.append(
            f"PDF 共 {total} 页，本次只处理前 {limit} 页（上限见 config.ocr.max_pages_per_run）。"
        )

    for index in range(limit):
        page = doc[index]
        page_no = index + 1
        text = page.get_text("text") or ""
        images = page.get_images(full=True)
        area = abs(page.rect.width * page.rect.height)
        need, reason = needs_ocr(text, image_count=len(images), page_area=area)
        report = {
            "page": page_no,
            "char_count": len(re.sub(r"\s", "", text)),
            "image_count": len(images),
            "use_ocr": need,
            "reason": reason,
            "method": B.M_PDF_TEXT,
            "ocr_ok": None,
        }

        if not need:
            for block in page.get_text("blocks") or []:
                if len(block) < 7 or block[6] != 0:
                    continue
                content = (block[4] or "").strip()
                if not content:
                    continue
                blocks.append(builder.add(
                    content,
                    method=B.M_PDF_TEXT,
                    kind=B.KIND_PDF_LINE,
                    page=page_no,
                    locator={"bbox": [round(v, 2) for v in block[:4]], "block_no": block[5]},
                ))
            page_reports.append(report)
            continue

        # ---- OCR path ---------------------------------------------------
        image_path = _render_page(page, page_image_dir, source_id, page_no, dpi)
        if image_path:
            page_images[page_no] = str(image_path)
        if provider is None:
            report.update({"ocr_ok": False, "error": "no_ocr_provider"})
            ocr_failures.append({"page": page_no, "reason": "没有可用的 OCR 后端", "detail": reason})
            page_reports.append(report)
            continue
        try:
            ocr = provider.recognize(image_path, page=page_no, lang=lang, structure=structure)
        except OcrUnavailable as exc:
            report.update({"ocr_ok": False, "error": exc.code, "hint": exc.hint})
            ocr_failures.append({"page": page_no, "reason": exc.message, "detail": reason})
            page_reports.append(report)
            continue
        report.update({
            "method": B.M_OCR,
            "ocr_ok": True,
            "provider": ocr.provider,
            "model": ocr.model,
            "line_count": len(ocr.lines),
        })
        if ocr.warnings:
            report["ocr_warnings"] = ocr.warnings
        for line in ocr.lines:
            blocks.append(builder.add(
                line.text,
                method=B.M_FIXTURE if ocr.provider == "fixture" else B.M_OCR,
                kind=B.KIND_OCR_LINE if line.kind in {"text", "ocr_line"} else B.KIND_OCR_LAYOUT,
                page=page_no,
                locator={"bbox": line.bbox, "layout_type": line.kind,
                             "raw_text": line.raw_text},
                confidence=line.confidence,
            ))
        page_reports.append(report)

    doc.close()

    ordering = B.detect_reading_order_conflicts(blocks)
    result.blocks = blocks
    result.report = {
        "pages_total": total,
        "pages_processed": limit,
        "ocr_pages": [p["page"] for p in page_reports if p["use_ocr"]],
        "text_pages": [p["page"] for p in page_reports if not p["use_ocr"]],
        "page_reports": page_reports,
        "page_images": page_images,
        "reading_order_conflicts": ordering,
        "has_pages": True,
        "ocr_used": any(p["use_ocr"] for p in page_reports),
        "ocr_failures": ocr_failures,
    }
    if ocr_failures:
        failed = ", ".join(str(f["page"]) for f in ocr_failures)
        result.errors.append(
            f"第 {failed} 页需要 OCR，但没有可用的识别后端；这些页的内容缺失。"
        )
    if ordering:
        result.warnings.append(
            f"检测到 {len(ordering)} 处阅读顺序冲突，相关块已标记待确认。"
        )
        ids = {c["a_block_id"] for c in ordering} | {c["b_block_id"] for c in ordering}
        for block in blocks:
            if block.block_id in ids and block.check_status == B.CHECK_OK:
                block.check_status = B.CHECK_REVIEW
                block.flags.append("reading_order_conflict")
    if not blocks:
        result.errors.append("PDF 未提取到任何文字。")
    return result


def _render_page(page, out_dir: Path, source_id: str, page_no: int, dpi: int) -> Path | None:
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None
    try:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        path = out_dir / f"{source_id}-p{page_no:03d}.png"
        pix.save(str(path))
        return path
    except Exception:
        return None


def page_count(path: Path) -> int:
    doc = open_pdf(path)
    try:
        return doc.page_count
    finally:
        doc.close()

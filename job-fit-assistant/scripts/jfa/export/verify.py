# -*- coding: utf-8 -*-
"""Post-export verification.

Every export is re-opened and checked, because "the file exists" is not
"the resume is correct":

  * re-extract the PDF text and compare it with what we meant to write;
  * confirm DOCX and PDF agree with each other;
  * rasterise each page and look for blank pages;
  * flag text outside the printable area, overlaps, tiny fonts, tofu glyphs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..errors import DependencyMissing, ExportError
from ..rewrite import render_text
from ..util import compare_key, loose_key, squash, truncate

CHECK_PASS = "pass"
CHECK_WARN = "warn"
CHECK_FAIL = "fail"

BLANK_INK_RATIO = 0.0015
MIN_LINE_COVERAGE = 0.90
MIN_CHAR_COVERAGE = 0.85
TINY_FONT_TOLERANCE = 0.4
OVERLAP_RATIO = 0.30
MARGIN_TOLERANCE_PT = 3.0

_TOFU = re.compile(r"[\ufffd\u25a1\u25a0]")


def verify_export(
    *,
    pdf_path: Path,
    docx_path: Path,
    doc_model: dict,
    render_dir: Path,
    max_pages: int = 2,
    min_font_pt: float = 9.5,
    expected_text: str | None = None,
) -> dict:
    checks: list[dict] = []
    pages_report: list[dict] = []

    def add(cid: str, name: str, status: str, detail: str, **extra):
        checks.append({"id": cid, "name": name, "status": status,
                       "detail": detail, **extra})

    expected = expected_text or render_text(doc_model)
    expected_lines = [ln for ln in (l.strip() for l in expected.split("\n")) if ln]
    expected_key = compare_key("".join(expected_lines))

    # ---------------------------------------------------------- DOCX checks
    docx_text = ""
    try:
        docx_text = _docx_text(docx_path)
        add("docx_readable", "DOCX 可重新读取", CHECK_PASS,
            f"提取到 {len(docx_text)} 个字符")
    except Exception as exc:
        add("docx_readable", "DOCX 可重新读取", CHECK_FAIL, f"无法读取 DOCX：{exc}")

    if docx_text:
        missing = [ln for ln in expected_lines if loose_key(ln) not in loose_key(docx_text)]
        status = CHECK_PASS if not missing else CHECK_FAIL
        add("docx_complete", "DOCX 内容完整", status,
            "全部段落写入成功" if not missing
            else f"{len(missing)} 段未出现在 DOCX 中，例如：{truncate(missing[0], 40)}",
            missing=missing[:10])

    # ----------------------------------------------------------- PDF checks
    if not Path(pdf_path).exists():
        add("pdf_exists", "PDF 已生成", CHECK_FAIL, "PDF 文件不存在")
        return _result(checks, pages_report)

    size = Path(pdf_path).stat().st_size
    if size < 1024:
        add("pdf_exists", "PDF 已生成", CHECK_FAIL, f"PDF 仅 {size} 字节，疑似空文件")
        return _result(checks, pages_report)
    add("pdf_exists", "PDF 已生成", CHECK_PASS, f"{size / 1024:.0f} KB")

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise DependencyMissing(
            "缺少 PyMuPDF，无法执行导出后校验。",
            hint="pip install -r scripts/requirements.txt",
        ) from exc

    doc = pymupdf.open(str(pdf_path))
    try:
        page_count = doc.page_count
        if page_count == 0:
            add("pdf_pages", "PDF 页数", CHECK_FAIL, "PDF 没有任何页面")
            return _result(checks, pages_report)
        if page_count > max_pages:
            add("pdf_pages", "PDF 页数", CHECK_WARN,
                f"共 {page_count} 页，超过设定的 {max_pages} 页；未通过缩小字号强行压缩",
                pages=page_count)
        else:
            add("pdf_pages", "PDF 页数", CHECK_PASS, f"共 {page_count} 页")

        page_texts: list[str] = []
        blank_pages: list[int] = []
        overflow_pages: list[int] = []
        overlap_pages: list[int] = []
        tofu_pages: list[int] = []
        tiny_font_pages: list[int] = []
        min_size_seen = 999.0

        for index in range(page_count):
            page = doc[index]
            page_no = index + 1
            text = page.get_text("text") or ""
            page_texts.append(text)
            rect = page.rect
            data = page.get_text("dict")

            overflow: list[str] = []
            overlaps: list[int] = []
            tiny: list[float] = []
            line_boxes: list[tuple[float, float, float, float, str]] = []
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    bbox = line.get("bbox") or (0, 0, 0, 0)
                    content = "".join(span.get("text", "") for span in line.get("spans", []))
                    if not content.strip():
                        continue
                    in_footer = bbox[1] > rect.height - 45
                    for span in line.get("spans", []):
                        size_pt = float(span.get("size") or 0)
                        if size_pt:
                            # page furniture (page numbers) is not resume body
                            if not in_footer:
                                min_size_seen = min(min_size_seen, size_pt)
                                if size_pt < min_font_pt - TINY_FONT_TOLERANCE:
                                    tiny.append(size_pt)
                    line_boxes.append((bbox[0], bbox[1], bbox[2], bbox[3], content))
                    if (bbox[0] < MARGIN_TOLERANCE_PT - 3
                            or bbox[1] < MARGIN_TOLERANCE_PT - 3
                            or bbox[2] > rect.width - MARGIN_TOLERANCE_PT + 3
                            or bbox[3] > rect.height - MARGIN_TOLERANCE_PT + 3):
                        overflow.append(truncate(content, 30))

            for i in range(len(line_boxes)):
                for j in range(i + 1, len(line_boxes)):
                    if _overlap(line_boxes[i], line_boxes[j]) > OVERLAP_RATIO:
                        overlaps.append(i)
                        break

            ink = _ink_ratio(Path(render_dir) / f"page-{page_no:02d}.png")
            pages_report.append({
                "page": page_no,
                "chars": len(squash(text)),
                "ink_ratio": ink,
                "min_font_pt": min((b[0] for b in []), default=None),
                "overflow_lines": overflow,
                "overlaps": len(overlaps),
                "tiny_fonts": tiny,
            })
            if ink is not None and ink < BLANK_INK_RATIO:
                blank_pages.append(page_no)
            if overflow:
                overflow_pages.append(page_no)
            if overlaps:
                overlap_pages.append(page_no)
            if _TOFU.search(text):
                tofu_pages.append(page_no)
            if tiny:
                tiny_font_pages.append(page_no)

        full_pdf_text = "\n".join(page_texts)
        pdf_key = compare_key(full_pdf_text)

        # blank pages
        add("pdf_blank_pages", "空白页检查",
            CHECK_FAIL if blank_pages else CHECK_PASS,
            f"检测到空白页 {blank_pages}" if blank_pages else "每页都有实际内容",
            pages=blank_pages)

        # garbled
        add("pdf_glyphs", "乱码检查",
            CHECK_FAIL if tofu_pages else CHECK_PASS,
            f"第 {tofu_pages} 页含替换字符/豆腐块" if tofu_pages else "未发现乱码字符",
            pages=tofu_pages)

        # overflow / truncation
        add("pdf_overflow", "溢出与截断检查",
            CHECK_FAIL if overflow_pages else CHECK_PASS,
            f"第 {overflow_pages} 页有文字超出可打印区域（可能被截断）"
            if overflow_pages else "所有文字都在页边距内",
            pages=overflow_pages)

        # overlap
        add("pdf_overlap", "重叠检查",
            CHECK_FAIL if overlap_pages else CHECK_PASS,
            f"第 {overlap_pages} 页存在文字重叠" if overlap_pages
            else "未发现文字重叠",
            pages=overlap_pages)

        # font floor
        add("pdf_font_floor", "字号下限检查",
            CHECK_WARN if tiny_font_pages else CHECK_PASS,
            f"第 {tiny_font_pages} 页出现小于 {min_font_pt}pt 的文字"
            if tiny_font_pages else f"最小字号 {min_size_seen:.1f}pt，未低于 {min_font_pt}pt",
            pages=tiny_font_pages, min_size=round(min_size_seen, 2))

        # line coverage
        if expected_lines:
            found = 0
            missing_lines: list[str] = []
            pdf_loose = loose_key(full_pdf_text)
            for line in expected_lines:
                key = loose_key(line)
                if not key:
                    continue
                if key in pdf_loose:
                    found += 1
                else:
                    # a wrapped bullet is still the same text
                    head = key[: max(6, len(key) // 2)]
                    if head in pdf_loose:
                        found += 1
                    else:
                        missing_lines.append(line)
            ratio = found / len(expected_lines)
            add("pdf_line_coverage", "文字遗漏检查",
                CHECK_PASS if ratio >= MIN_LINE_COVERAGE else CHECK_FAIL,
                f"预期 {len(expected_lines)} 行，PDF 中可定位 {found} 行（{ratio:.1%}）"
                + (f"；缺失示例：{truncate(missing_lines[0], 40)}" if missing_lines else ""),
                ratio=round(ratio, 3), missing=missing_lines[:10])

        # per-page non-empty for multi-page
        empty_pages = [i + 1 for i, t in enumerate(page_texts) if len(squash(t)) < 5]
        if page_count > 1:
            add("pdf_page_text", "分页文字检查",
                CHECK_FAIL if empty_pages else CHECK_PASS,
                f"第 {empty_pages} 页几乎没有文字" if empty_pages else "每页都有可提取文字",
                pages=empty_pages)

        # DOCX vs PDF consistency
        if docx_text:
            docx_key = loose_key(docx_text)
            same = docx_key == loose_key(full_pdf_text)
            if not same:
                common = _char_coverage(docx_key, loose_key(full_pdf_text))
                add("docx_pdf_consistency", "DOCX 与 PDF 一致性",
                    CHECK_PASS if common >= 0.97 else CHECK_FAIL,
                    f"字符覆盖率 {common:.1%}" + ("" if common >= 0.97 else "，两者内容存在差异"),
                    ratio=round(common, 3))
            else:
                add("docx_pdf_consistency", "DOCX 与 PDF 一致性", CHECK_PASS, "两者文本完全一致")

        # selectable text (searchability)
        add("pdf_selectable", "PDF 文字可选中/可搜索",
            CHECK_PASS if len(squash(full_pdf_text)) > 50 else CHECK_FAIL,
            f"可提取 {len(squash(full_pdf_text))} 字")
    finally:
        doc.close()

    return _result(checks, pages_report)


def _result(checks: list[dict], pages: list[dict]) -> dict:
    failures = [c for c in checks if c["status"] == CHECK_FAIL]
    warnings = [c for c in checks if c["status"] == CHECK_WARN]
    return {
        "ok": not failures,
        "checks": checks,
        "pages": pages,
        "failures": failures,
        "warnings": warnings,
        "summary": {
            "passed": sum(1 for c in checks if c["status"] == CHECK_PASS),
            "failed": len(failures),
            "warned": len(warnings),
            "verdict": "导出校验通过" if not failures else "导出校验未通过",
        },
    }


def _docx_text(path: Path) -> str:
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _overlap(a, b) -> float:
    ax0, ay0, ax1, ay1 = a[:4]
    bx0, by0, bx1, by1 = b[:4]
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return inter / smaller if smaller > 0 else 0.0


def _ink_ratio(image_path: Path) -> float | None:
    if not image_path.exists():
        return None
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            gray = img.convert("L")
            hist = gray.histogram()
            total = sum(hist)
            dark = sum(hist[:240])
            return round(dark / total, 6) if total else None
    except Exception:
        return None


def _char_coverage(a: str, b: str) -> float:
    """Share of characters in `a` that appear in `b`, order-insensitive."""
    if not a:
        return 1.0
    pool: dict[str, int] = {}
    for ch in b:
        pool[ch] = pool.get(ch, 0) + 1
    hit = 0
    for ch in a:
        if pool.get(ch, 0) > 0:
            pool[ch] -= 1
            hit += 1
    return hit / len(a)


def render_report(report: dict) -> str:
    lines = ["## 导出校验", "", f"结论：{report['summary']['verdict']}", ""]
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("| --- | --- | --- |")
    icon = {CHECK_PASS: "通过", CHECK_WARN: "警告", CHECK_FAIL: "失败"}
    for check in report["checks"]:
        lines.append(f"| {check['name']} | {icon.get(check['status'], check['status'])} | {check['detail']} |")
    if report.get("pages"):
        lines.append("")
        lines.append("| 页 | 字数 | 墨水占比 | 溢出 | 重叠 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for page in report["pages"]:
            lines.append(
                f"| {page['page']} | {page['chars']} | {page.get('ink_ratio')} | "
                f"{len(page.get('overflow_lines') or [])} | {page.get('overlaps')} |"
            )
    return "\n".join(lines)

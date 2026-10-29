# -*- coding: utf-8 -*-
"""DOCX extraction: paragraphs, tables, text boxes, headers/footers, images.

`python-docx` alone silently drops text boxes, header/footer content and
embedded images. That is the classic way a resume loses a phone number. This
module pulls those out explicitly and, where it still cannot read something
(charts, OLE objects, SmartArt), records it as unhandled content with a warning
instead of ignoring it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import blocks as B
from ..errors import DependencyMissing
from . import SourceResult

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "o": "urn:schemas-microsoft-com:office:office",
}

#: XML tags whose text this extractor does not read
UNHANDLED_TAGS = {
    "w:object": "OLE 嵌入对象（如图表、附件）",
    "w:footnoteReference": "脚注",
    "w:endnoteReference": "尾注",
    "w:commentReference": "批注",
    "c:chart": "图表（文字在 Excel 数据源里，正文中不可见）",
    "a:graphicData": "嵌入式图形对象",
}


def extract(path: Path, *, source_id: str) -> SourceResult:
    try:
        import docx  # type: ignore
        from docx.table import Table  # type: ignore
        from docx.text.paragraph import Paragraph  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise DependencyMissing(
            "未安装 python-docx，无法解析 DOCX。",
            hint="pip install -r scripts/requirements.txt",
        ) from exc

    result = SourceResult(source_id=source_id, path=path, kind="docx")
    builder = B.BlockBuilder(source_id)
    document = docx.Document(str(path))
    blocks: list[B.Block] = []
    counters = {"paragraph": 0, "table": 0, "table_row": 0, "textbox": 0,
                "image": 0, "header": 0, "footer": 0}

    # ---- body in document order (paragraphs and tables interleaved) -----
    body = document.element.body
    for child in body.iterchildren():
        tag = _local(child.tag)
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = (paragraph.text or "").strip()
            counters["paragraph"] += 1
            if not text:
                continue
            blocks.append(builder.add(
                text,
                method=B.M_DOCX_PARAGRAPH,
                kind=B.KIND_HEADING if _is_heading(paragraph) else B.KIND_PARAGRAPH,
                page=None,
                locator={"docx_paragraph": counters["paragraph"],
                         "style": _style_name(paragraph)},
            ))
        elif tag == "tbl":
            table = Table(child, document)
            counters["table"] += 1
            for r_idx, row in enumerate(table.rows, 1):
                cells = [(c.text or "").strip() for c in row.cells]
                joined = " | ".join(c for c in cells if c)
                if not joined:
                    continue
                counters["table_row"] += 1
                blocks.append(builder.add(
                    joined,
                    method=B.M_DOCX_TABLE,
                    kind=B.KIND_TABLE_ROW,
                    page=None,
                    locator={"docx_table": counters["table"], "docx_row": r_idx,
                             "empty_cells": sum(1 for c in cells if not c)},
                ))

    # ---- text boxes ------------------------------------------------------
    for node in _xpath(document.element, ".//w:txbxContent//w:p"):
        text = "".join(node.xpath(".//w:t/text()") if hasattr(node, "xpath") else []).strip()
        if not text:
            text = "".join(t.text or "" for t in node.iter() if _local(t.tag) == "t").strip()
        if not text:
            continue
        counters["textbox"] += 1
        blocks.append(builder.add(
            text,
            method=B.M_DOCX_TEXTBOX,
            kind=B.KIND_TEXTBOX,
            page=None,
            locator={"docx_textbox": counters["textbox"]},
        ))
    if counters["textbox"]:
        result.warnings.append(
            f"DOCX 中有 {counters['textbox']} 处文本框内容，已单独提取并标记为待核对"
            "（普通解析器通常会漏掉它们）。"
        )

    # ---- headers & footers ----------------------------------------------
    for s_idx, section in enumerate(document.sections, 1):
        for kind, container in (("header", section.header), ("footer", section.footer)):
            try:
                paras = container.paragraphs
            except Exception:
                continue
            for p_idx, paragraph in enumerate(paras, 1):
                text = (paragraph.text or "").strip()
                if not text:
                    continue
                counters[kind] += 1
                blocks.append(builder.add(
                    text,
                    method=B.M_DOCX_PARAGRAPH,
                    kind=B.KIND_PARAGRAPH,
                    page=None,
                    locator={f"docx_{kind}": s_idx, "paragraph": p_idx},
                ))

    # ---- embedded images -------------------------------------------------
    image_parts = [part for part in document.part.related_parts.values()
                   if getattr(part, "content_type", "").startswith("image/")]
    for idx, part in enumerate(image_parts, 1):
        counters["image"] += 1
        blocks.append(builder.add(
            f"[嵌入图片 {idx}] {getattr(part, 'partname', '')}",
            method=B.M_DOCX_IMAGE,
            kind=B.KIND_IMAGE,
            page=None,
            locator={"docx_image": idx, "content_type": getattr(part, "content_type", "")},
        ))
    if counters["image"]:
        result.warnings.append(
            f"DOCX 中检测到 {counters['image']} 张嵌入图片（可能是截图版经历/证书/照片）。"
            "本工具不会自动识别图片内文字；如需读取，请把该图片单独作为图片文件提交 OCR，"
            "或由用户确认图片是否含关键信息。"
        )

    # ---- content we do not read -----------------------------------------
    unhandled = _scan_unhandled(document.element)
    if unhandled:
        result.warnings.append(
            "DOCX 中存在本工具不解析的内容：" + "；".join(unhandled)
            + "。这些内容可能包含信息，未计入事实档案，请人工确认是否需要补充。"
        )

    # ---- multi-column layout -------------------------------------------
    columns = _column_counts(document)
    if columns and max(columns) > 1:
        result.warnings.append(
            f"检测到分栏排版（每节栏数 {columns}）。文字按文档流顺序提取，"
            "可能与视觉阅读顺序不一致，请对照原件确认经历/教育是否串行。"
        )

    result.blocks = blocks
    result.report = {
        "counters": counters,
        "has_pages": False,
        "ocr_used": False,
        "page_note": "DOCX 没有固定页码，定位使用段落/表格/文本框序号，不编造页码。",
        "unhandled": unhandled,
        "images": counters["image"],
        "textboxes": counters["textbox"],
        "columns": columns,
    }
    if not blocks:
        result.errors.append("DOCX 中没有提取到任何文字。")
    return result


def _column_counts(document) -> list[int]:
    """Columns per section (`w:cols@w:num`). A 2-column resume reorders text."""
    from docx.oxml.ns import qn

    counts: list[int] = []
    for section in document.sections:
        try:
            sect_pr = section._sectPr
        except Exception:
            continue
        cols = sect_pr.find(qn("w:cols"))
        if cols is None:
            counts.append(1)
            continue
        raw = cols.get(qn("w:num"))
        try:
            counts.append(int(raw) if raw else 1)
        except ValueError:
            counts.append(1)
    return counts


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _style_name(paragraph) -> str:
    try:
        return paragraph.style.name if paragraph.style is not None else ""
    except Exception:
        return ""


def _is_heading(paragraph) -> bool:
    name = _style_name(paragraph).lower()
    if name.startswith("heading") or name.startswith("标题"):
        return True
    text = (paragraph.text or "").strip()
    if len(text) > 30 or not text:
        return False
    return bool(re.match(r"^(教育|工作|实习|项目|技能|荣誉|证书|自我|求职|个人|校园|实践)", text))


def _xpath(element, path: str) -> list:
    try:
        found = element.xpath(path)
        if found is not None:
            return list(found)
    except Exception:
        pass
    try:
        from lxml import etree

        tree = etree.ElementTree(element)
        return list(tree.xpath(path, namespaces=NS))
    except Exception:
        return []


def _scan_unhandled(element) -> list[str]:
    found: list[str] = []
    try:
        from lxml import etree

        tree = etree.ElementTree(element)
        for tag, label in UNHANDLED_TAGS.items():
            prefix, name = tag.split(":")
            ns = NS.get(prefix, "")
            hits = tree.xpath(f".//*[local-name()='{name}']")
            if hits:
                found.append(f"{label} ×{len(hits)}")
    except Exception:
        pass
    return found

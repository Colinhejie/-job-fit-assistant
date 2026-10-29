# -*- coding: utf-8 -*-
"""Plain text (.txt / .md) extraction — the phase-1 path."""

from __future__ import annotations

import re
from pathlib import Path

from .. import blocks as B
from . import SourceResult

_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|【.+】\s*$)")


def extract(path: Path, *, source_id: str) -> SourceResult:
    result = SourceResult(source_id=source_id, path=path, kind="text")
    raw = _read_text(path)
    builder = B.BlockBuilder(source_id)
    blocks: list[B.Block] = []

    # Resumes are line-structured: one line carries one field (company|title|date,
    # a bullet, a skill list). Grouping by blank lines would merge a whole
    # section into one block and destroy that structure.
    for lineno, line in enumerate(raw.split("\n"), 1):
        text = line.strip()
        if not text:
            continue
        kind = B.KIND_HEADING if _is_heading(text) else B.KIND_PARAGRAPH
        blocks.append(builder.add(
            text,
            method="plain_text",
            kind=kind,
            page=None,
            locator={"line": lineno},
        ))

    result.blocks = blocks
    result.report = {
        "chars": len(raw),
        "paragraphs": len(blocks),
        "has_pages": False,
        "ocr_used": False,
        "note": "纯文本没有页码；定位信息使用行号。",
    }
    if not blocks:
        result.errors.append("文件里没有可读文本（可能是空文件）。")
    return result


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _paragraphs(text: str) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip():
            buffer.append(line.rstrip())
        elif buffer:
            chunks.append("\n".join(buffer))
            buffer = []
    if buffer:
        chunks.append("\n".join(buffer))
    return chunks


def _is_heading(chunk: str) -> bool:
    first = chunk.split("\n")[0].strip()
    if len(chunk) > 60:
        return False
    return bool(_HEADING.match(first)) or len(first) <= 14


def _line_of(raw: str, chunk: str) -> int:
    idx = raw.find(chunk)
    return raw.count("\n", 0, idx) + 1 if idx >= 0 else 0

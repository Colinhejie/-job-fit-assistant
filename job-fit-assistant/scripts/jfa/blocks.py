# -*- coding: utf-8 -*-
"""Block model for extracted resume content.

A block is the smallest unit the fact archive can cite. Resumes arrive through
several extractors; every block records which one produced it and whether a
human still has to look at it. Page numbers are only ever filled in when the
source format actually has pages (PDF/image) — never invented for DOCX.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator

from .util import now_iso, sha256_text

# block kinds
KIND_PARAGRAPH = "paragraph"
KIND_HEADING = "heading"
KIND_TABLE_CELL = "table_cell"
KIND_TABLE_ROW = "table_row"
KIND_TEXTBOX = "textbox"
KIND_IMAGE = "image"
KIND_PDF_LINE = "pdf_line"
KIND_OCR_LINE = "ocr_line"
KIND_OCR_LAYOUT = "ocr_layout"

# extraction methods (persisted verbatim — audit trail)
M_DOCX_PARAGRAPH = "docx_paragraph"
M_DOCX_TABLE = "docx_table"
M_DOCX_TEXTBOX = "docx_textbox"
M_DOCX_IMAGE = "docx_embedded_image"
M_PDF_TEXT = "pdf_text_layer"
M_PDF_RENDER = "pdf_page_render"
M_IMAGE = "image_file"
M_OCR = "ocr"
M_FIXTURE = "fixture_test_double"  # test-only, never a real recognition claim
M_USER = "user_correction"

# check status
CHECK_OK = "ok"
CHECK_REVIEW = "needs_review"
CHECK_CONFIRMED = "user_confirmed"
CHECK_CORRECTED = "user_corrected"
CHECK_UNVERIFIED = "unverified"

CHECK_VALUES = (CHECK_OK, CHECK_REVIEW, CHECK_CONFIRMED, CHECK_CORRECTED, CHECK_UNVERIFIED)

#: methods that are a machine *guess* and therefore always need a human look
SPECULATIVE_METHODS = {M_OCR, M_FIXTURE}

_REPLACEMENT_RE = re.compile("[\ufffd\u25a1\u25a0]")
_OK_PUNCT = "，。、；：\u201c\u201d\u2018\u2019（）()[]【】《》·-—–/.,%+&@#:;!?"
_BAD_RATIO_CHARS = re.compile(rf"[^\w\s\u4e00-\u9fff{re.escape(_OK_PUNCT)}]")
_DATE_LIKE = re.compile(
    r"(?<!\d)(19|20)(\d{2})\s*[年./\-]\s*(\d{1,2})(?!\d)"
)


@dataclass
class Block:
    block_id: str
    source_id: str
    text: str
    method: str
    kind: str = KIND_PARAGRAPH
    page: int | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    check_status: str = CHECK_OK
    order: int = 0
    flags: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["text_sha256"] = sha256_text(self.text)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


class BlockBuilder:
    """Allocates ids and applies the automatic review heuristics."""

    def __init__(self, source_id: str, *, start_order: int = 1):
        self.source_id = source_id
        self._n = 0
        self._order = start_order

    def add(
        self,
        text: str,
        *,
        method: str,
        kind: str = KIND_PARAGRAPH,
        page: int | None = None,
        locator: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> Block:
        self._n += 1
        block = Block(
            block_id=f"B-{self._n:04d}",
            source_id=self.source_id,
            text=text,
            method=method,
            kind=kind,
            page=page,
            locator=locator or {},
            confidence=confidence,
            order=self._order,
        )
        self._order += 1
        annotate(block)
        return block


# ---------------------------------------------------------------------------
# heuristics: what a human has to look at
# ---------------------------------------------------------------------------

LOW_CONFIDENCE = 0.75
MIN_USEFUL_CHARS = 2


def annotate(block: Block) -> Block:
    """Set `check_status`/`flags` for one block. Deterministic and explainable."""
    flags: list[str] = []
    text = block.text or ""
    stripped = text.strip()

    if block.method in SPECULATIVE_METHODS:
        flags.append("machine_guess")
    if block.confidence is not None and block.confidence < LOW_CONFIDENCE:
        flags.append("low_confidence")
    if _REPLACEMENT_RE.search(text):
        flags.append("garbled_glyph")
    if stripped and len(stripped) <= MIN_USEFUL_CHARS and block.kind in {KIND_PDF_LINE, KIND_OCR_LINE}:
        flags.append("too_short")
    if stripped:
        bad = len(_BAD_RATIO_CHARS.findall(stripped))
        if bad / max(len(stripped), 1) > 0.35:
            flags.append("garbled_glyph")
    for year, month in suspicious_dates(stripped):
        flags.append(f"odd_date:{year}-{month:02d}")
    if block.page is None and block.method in {M_OCR, M_PDF_TEXT}:
        flags.append("missing_page_ref")

    block.flags = flags

    blocking = {"garbled_glyph", "low_confidence", "too_short"}
    if any(f in blocking or f.startswith("odd_date") for f in flags):
        block.check_status = CHECK_REVIEW
    elif block.method in SPECULATIVE_METHODS:
        block.check_status = CHECK_UNVERIFIED
    elif block.method in {M_DOCX_TEXTBOX, M_DOCX_IMAGE}:
        # textboxes/images are reported, not silently trusted as body text
        block.check_status = CHECK_REVIEW
    else:
        block.check_status = CHECK_OK
    return block


def suspicious_dates(text: str) -> list[tuple[int, int]]:
    """Month-like fragments that cannot be a real calendar month/year."""
    bad: list[tuple[int, int]] = []
    current_year = int(now_iso()[:4])
    for m in _DATE_LIKE.finditer(text):
        year = int(m.group(1) + m.group(2))
        month = int(m.group(3))
        if month < 1 or month > 12:
            bad.append((year, month))
        elif year < 1950 or year > current_year + 10:
            bad.append((year, month))
    return bad


# ---------------------------------------------------------------------------
# collection helpers
# ---------------------------------------------------------------------------


def iter_review(blocks: Iterable[Block]) -> Iterator[Block]:
    for b in blocks:
        if b.check_status in {CHECK_REVIEW, CHECK_UNVERIFIED}:
            yield b


def group_by_page(blocks: Iterable[Block]) -> dict[int | None, list[Block]]:
    pages: dict[int | None, list[Block]] = {}
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    return pages


def detect_reading_order_conflicts(blocks: list[Block]) -> list[dict]:
    """Flag pages where block order disagrees with vertical position.

    Two independent signals, both cheap and explainable:
      1. block order says A is before B, but B's top edge is clearly above A's.
      2. a block's bbox is fully above a block already emitted on the same page.
    Only runs when boxes exist; DOCX has no bboxes and is never flagged.
    """
    conflicts: list[dict] = []
    by_page = group_by_page(blocks)
    for page, page_blocks in by_page.items():
        with_box = [b for b in page_blocks if "bbox" in (b.locator or {})]
        if len(with_box) < 2:
            continue
        tops = [(b.order, b.locator["bbox"][1], b.block_id) for b in with_box
                if isinstance(b.locator.get("bbox"), (list, tuple)) and len(b.locator["bbox"]) == 4]
        for i in range(1, len(tops)):
            prev_order, prev_top, prev_id = tops[i - 1]
            cur_order, cur_top, cur_id = tops[i]
            if cur_order > prev_order and cur_top < prev_top - 12:
                conflicts.append({
                    "page": page,
                    "a_block_id": prev_id,
                    "b_block_id": cur_id,
                    "reason": "阅读顺序冲突：后输出的块位置明显更靠上",
                    "delta_px": round(prev_top - cur_top, 1),
                })
        for b in with_box:
            bbox = b.locator.get("bbox")
            if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
                continue
            x0, y0, x1, y1 = bbox
            if x1 <= x0 or y1 <= y0:
                conflicts.append({
                    "page": page,
                    "a_block_id": b.block_id,
                    "b_block_id": b.block_id,
                    "reason": "坐标框退化（宽或高 <= 0）",
                    "delta_px": 0,
                })
    return conflicts

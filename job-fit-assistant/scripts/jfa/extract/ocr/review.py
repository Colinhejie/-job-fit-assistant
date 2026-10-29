# -*- coding: utf-8 -*-
"""OCR review workflow.

Recognition is not truth. This module builds a review queue that shows the
recognised text next to the source page image, marks the spans that matter
(name, contact, company, school, dates, numbers) and records corrections as
revisions instead of silently overwriting what the OCR said.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ... import blocks as B
from ...errors import NotFoundError, ValidationError
from ...util import append_jsonl, now_iso, read_json, read_jsonl, squash, truncate, write_json

SCHEMA_QUEUE = "jfa.ocr_review/1"

ITEM_PENDING = "pending"
ITEM_CONFIRMED = "confirmed"
ITEM_CORRECTED = "corrected"
ITEM_REJECTED = "rejected"
ITEM_STATES = (ITEM_PENDING, ITEM_CONFIRMED, ITEM_CORRECTED, ITEM_REJECTED)

CATEGORY_LABELS = {
    "contact": "联系方式",
    "name": "姓名",
    "company": "公司",
    "school": "学校",
    "degree": "学历",
    "date": "日期",
    "number": "数字",
    "garbled": "疑似乱码",
}

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("contact", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("contact", re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")),
    ("contact", re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")),
    ("date", re.compile(r"(?<!\d)(?:19|20)\d{2}\s*[年./\-]\s*\d{1,2}(?:\s*[月./\-]\s*\d{1,2}\s*日?)?")),
    ("date", re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")),
    ("school", re.compile(r"[\u4e00-\u9fff]{2,12}(?:大学|学院|学校|中学|高中)")),
    ("school", re.compile(r"[A-Za-z][A-Za-z\s.&\-]{3,40}(?:University|College|Institute)")),
    ("company", re.compile(r"[\u4e00-\u9fff]{2,20}(?:有限公司|股份有限公司|集团|科技|网络|信息技术|工作室)")),
    ("degree", re.compile(r"博士|硕士|本科|学士|大专|专科|高中|中专|MBA|EMBA")),
    ("number", re.compile(r"(?<!\d)\d+(?:\.\d+)?\s*(?:余|多)?\s*(?:%|万|亿|千|人|次|年|个月|倍|个|条|份|项|单|台|套|k|K|w|W)(?!\d)")),
    ("garbled", re.compile(r"[\ufffd\u25a1\u25a0]+")),
]

_NAME_LABEL = re.compile(r"(?:姓名|名字|name)\s*[:：]?\s*([\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z .'\-]{1,30})", re.I)


def highlight(text: str) -> list[dict]:
    """Non-overlapping spans worth a human's attention, with reasons."""
    spans: list[dict] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    m = _NAME_LABEL.search(text)
    if m:
        start, end = m.span(1)
        spans.append({"start": start, "end": end, "text": m.group(1),
                      "category": "name", "label": CATEGORY_LABELS["name"],
                      "reason": "姓名是最容易识别错、也最不能错的字段"})
        taken.append((start, end))

    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if not free(start, end) or end - start <= 0:
                continue
            spans.append({
                "start": start, "end": end, "text": match.group(0),
                "category": category, "label": CATEGORY_LABELS[category],
                "reason": _reason(category),
            })
            taken.append((start, end))
    spans.sort(key=lambda s: s["start"])
    return spans


def _reason(category: str) -> str:
    return {
        "contact": "联系方式错一位就无法联系，必须逐字符核对",
        "company": "公司名不得由工具按上下文推测补全",
        "school": "学校名与学历不得由工具推测",
        "degree": "学历不得由上下文推断",
        "date": "异常日期会污染工作年限计算",
        "number": "业绩/薪资类数字不得编造或改写",
        "garbled": "识别出乱码，需要人眼确认原文",
    }.get(category, "需人工确认")


def build_queue(
    blocks: Iterable[B.Block],
    *,
    source_id: str,
    source_file: str,
    page_images: dict[int | None, str] | None = None,
) -> dict:
    """Create review items for every block the extractor could not vouch for."""
    page_images = page_images or {}
    items: list[dict] = []
    for block in blocks:
        if block.check_status not in {B.CHECK_REVIEW, B.CHECK_UNVERIFIED}:
            continue
        spans = highlight(block.text)
        items.append({
            "item_id": f"RV-{len(items) + 1:04d}",
            "block_id": block.block_id,
            "page": block.page,
            "source_id": source_id,
            "source_file": source_file,
            "method": block.method,
            "confidence": block.confidence,
            "text": block.text,
            "spans": spans,
            "page_image": page_images.get(block.page, ""),
            "flags": list(block.flags),
            "state": ITEM_PENDING,
            "corrected_text": "",
            "correction_note": "",
            "decided_at": "",
            "revisions": [],
        })
    return {
        "schema": SCHEMA_QUEUE,
        "source_id": source_id,
        "source_file": source_file,
        "generated_at": now_iso(),
        "items": items,
        "pending": len(items),
        "summary": _summary(items),
    }


def _summary(items: list[dict]) -> dict:
    by_cat: dict[str, int] = {}
    for item in items:
        for span in item["spans"]:
            by_cat[span["category"]] = by_cat.get(span["category"], 0) + 1
    return {
        "items": len(items),
        "flagged_spans": sum(by_cat.values()),
        "by_category": by_cat,
        "note": "标出的字段必须逐条人工确认；确认只代表你确认，不代表第三方核实。",
    }


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def load_queue(path) -> dict:
    data = read_json(path, None)
    if data is None:
        raise NotFoundError(
            "核对队列不存在。",
            hint="先运行 `jfa ocr queue` 生成核对清单。",
        )
    return data


def apply_decision(
    queue: dict,
    item_id: str,
    *,
    action: str,
    corrected_text: str = "",
    note: str = "",
    actor: str = "user",
) -> dict:
    """accept | correct | reject one item. Original text is never destroyed."""
    if action not in {"accept", "correct", "reject"}:
        raise ValidationError(f"未知的核对动作：{action}",
                              detail={"allowed": ["accept", "correct", "reject"]})
    item = None
    for row in queue.get("items", []):
        if row["item_id"] == item_id:
            item = row
            break
    if item is None:
        raise NotFoundError(f"核对项不存在：{item_id}")

    before = item.get("text", "")
    if action == "correct" and squash(corrected_text) == squash(before):
        raise ValidationError("更正内容与原文相同，无需记录更正。",
                              hint="如果只是确认无误，请使用 accept。")

    item.setdefault("revisions", []).append({
        "at": now_iso(),
        "actor": actor,
        "action": action,
        "before": before,
        "after": corrected_text if action == "correct" else before,
        "note": note,
    })
    if action == "accept":
        item["state"] = ITEM_CONFIRMED
    elif action == "correct":
        item["state"] = ITEM_CORRECTED
        item["corrected_text"] = corrected_text
        item["correction_note"] = note
    else:
        item["state"] = ITEM_REJECTED
    item["decided_at"] = now_iso()
    queue["pending"] = sum(1 for r in queue["items"] if r["state"] == ITEM_PENDING)
    queue["summary"] = _summary(queue["items"])
    return item


def effective_text(item: dict) -> str:
    """What downstream extraction should use: the correction if one exists."""
    if item["state"] == ITEM_CORRECTED:
        return item["corrected_text"]
    if item["state"] == ITEM_REJECTED:
        return ""
    return item["text"]


def apply_corrections_to_blocks(blocks: list[B.Block], queue: dict) -> tuple[list[B.Block], list[dict]]:
    """Return corrected blocks plus a revision log. Original text stays in `notes`."""
    by_block = {row["block_id"]: row for row in queue.get("items", [])}
    log: list[dict] = []
    for block in blocks:
        row = by_block.get(block.block_id)
        if not row:
            continue
        if row["state"] == ITEM_REJECTED:
            log.append({"block_id": block.block_id, "action": "rejected",
                        "reason": row.get("correction_note", "")})
            block.notes = (block.notes + " [用户判读为无效，已排除]").strip()
            block.text = ""
            block.check_status = B.CHECK_CORRECTED
            continue
        if row["state"] == ITEM_CORRECTED:
            original = row["text"]
            block.text = row["corrected_text"]
            block.notes = (block.notes + f" [原文: {truncate(original, 200)}]").strip()
            block.check_status = B.CHECK_CORRECTED
            log.append({"block_id": block.block_id, "action": "corrected",
                        "before": original, "after": row["corrected_text"]})
        elif row["state"] == ITEM_CONFIRMED:
            block.check_status = B.CHECK_CONFIRMED
            log.append({"block_id": block.block_id, "action": "confirmed"})
    return blocks, log


def render_review_markdown(queue: dict, *, limit: int = 40) -> str:
    """Human-facing checklist. Shows image path + text + highlighted spans."""
    lines = [
        f"# OCR 核对清单 — {queue.get('source_file', '')}",
        "",
        f"- 待确认条目：{queue.get('pending', 0)} / {len(queue.get('items', []))}",
        f"- 说明：{queue.get('summary', {}).get('note', '')}",
        "",
    ]
    items = queue.get("items", [])[:limit]
    if not items:
        lines.append("没有需要人工核对的内容。")
        return "\n".join(lines)
    for item in items:
        lines.append(f"## {item['item_id']}　第 {item['page'] if item['page'] is not None else '—'} 页"
                     f"　方法：{item['method']}"
                     f"　置信度：{item['confidence'] if item['confidence'] is not None else '未返回'}")
        if item.get("page_image"):
            lines.append(f"- 原件位置：`{item['page_image']}`")
        lines.append(f"- 识别原文：{item['text']}")
        if item.get("flags"):
            lines.append(f"- 标记原因：{', '.join(item['flags'])}")
        if item.get("spans"):
            lines.append("- 需重点核对：")
            for span in item["spans"]:
                lines.append(f"  - [{span['label']}] `{span['text']}` — {span['reason']}")
        lines.append(f"- 当前状态：{item['state']}")
        if item["state"] == ITEM_CORRECTED:
            lines.append(f"- 已更正为：{item['corrected_text']}")
        lines.append("")
    if len(queue.get("items", [])) > limit:
        lines.append(f"（仅显示前 {limit} 条，共 {len(queue['items'])} 条）")
    return "\n".join(lines)


def append_correction_log(path, entry: dict) -> None:
    append_jsonl(path, {"at": now_iso(), **entry})


def load_correction_log(path) -> list[dict]:
    return read_jsonl(path)


def save_queue(path, queue: dict) -> None:
    write_json(path, queue)

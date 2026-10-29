# -*- coding: utf-8 -*-
"""Dispatch by file type, copy sources immutably, persist blocks and review queues."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import blocks as B
from ..errors import DependencyMissing, NotFoundError, OcrUnavailable, UsageError, ValidationError
from ..util import (
    ensure_within,
    now_iso,
    read_json,
    read_jsonl,
    safe_component,
    sha256_file,
    short_hash,
    write_json,
    write_jsonl,
)
from . import SourceResult
from .ocr import base as ocr_base
from .ocr import review as review_mod

TEXT_EXT = {".txt", ".text", ".md", ".markdown"}
DOCX_EXT = {".docx"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED = TEXT_EXT | DOCX_EXT | PDF_EXT | IMAGE_EXT

#: formats we deliberately do not parse, with an actionable message
UNSUPPORTED_HINTS = {
    ".doc": "旧版 .doc 二进制格式不受支持：请用 Word 另存为 .docx 后重试。",
    ".wps": "WPS 专有格式不受支持：请另存为 .docx 后重试。",
    ".pages": "Apple Pages 格式不受支持：请导出为 DOCX 或 PDF 后重试。",
    ".rtf": "RTF 不受支持：请另存为 .docx 后重试。",
    ".heic": "HEIC 图片不受支持：请转换为 PNG/JPG 后重试。",
}


def register(ws, path: Path) -> tuple[str, Path]:
    """Copy the input into `sources/` under a content-addressed name."""
    path = Path(path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise NotFoundError(f"找不到文件：{path}")
    if path.suffix.lower() not in SUPPORTED:
        hint = UNSUPPORTED_HINTS.get(path.suffix.lower(), "")
        raise UsageError(
            f"不支持的文件类型：{path.suffix or '(无扩展名)'}",
            hint=hint or f"支持：{sorted(SUPPORTED)}",
        )
    digest = sha256_file(path)
    source_id = f"src-{short_hash(digest, 8)}"
    ws.sources_dir.mkdir(parents=True, exist_ok=True)
    target = ws.sources_dir / f"{source_id}-{safe_component(path.stem, max_len=40)}-{digest[:6]}{path.suffix.lower()}"
    ensure_within(ws.root, target)
    if not target.exists():
        shutil.copy2(path, target)
        # a hand-authored sidecar travels with its image (test double only)
        sidecar = path.with_name(path.name + ".ocr.json")
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(target.name + ".ocr.json"))
    return source_id, target


def ingest(
    ws,
    path: Path,
    *,
    provider_name: str = "auto",
    lang: str = "ch",
    dpi: int = 220,
    max_pages: int = 20,
    structure: bool = False,
    allow_ocr: bool = True,
) -> dict:
    """Extract one file. Raises when OCR is required but unavailable."""
    source_id, stored = register(ws, path)
    suffix = stored.suffix.lower()
    pages_dir = ws.sources_dir / "_pages" / source_id

    provider = None
    if suffix in IMAGE_EXT:
        if not allow_ocr:
            raise ValidationError(
                "已禁用 OCR，但输入是图片文件。",
                hint="图片没有文字层；请改用 DOCX/文字型 PDF，或启用 OCR。",
            )
        provider = ocr_base.select(provider_name, structure=structure)
        from . import image as image_mod

        result = image_mod.extract(stored, source_id=source_id, provider=provider,
                                   lang=lang, structure=structure)
    elif suffix in PDF_EXT:
        from . import pdf as pdf_mod

        want_ocr = False
        try:
            doc = pdf_mod.open_pdf(stored)
            try:
                for index in range(min(doc.page_count, max_pages)):
                    page = doc[index]
                    need, _ = pdf_mod.needs_ocr(
                        page.get_text("text") or "",
                        image_count=len(page.get_images(full=True)),
                        page_area=abs(page.rect.width * page.rect.height),
                    )
                    if need:
                        want_ocr = True
                        break
            finally:
                doc.close()
        except DependencyMissing:
            raise
        if want_ocr and allow_ocr:
            try:
                provider = ocr_base.select(provider_name, structure=structure)
            except OcrUnavailable:
                provider = None
        result = pdf_mod.extract(
            stored, source_id=source_id, page_image_dir=pages_dir,
            provider=provider, lang=lang, dpi=dpi, max_pages=max_pages,
            structure=structure,
        )
    elif suffix in DOCX_EXT:
        from . import docx as docx_mod

        result = docx_mod.extract(stored, source_id=source_id)
    elif suffix in TEXT_EXT:
        from . import plain as plain_mod

        result = plain_mod.extract(stored, source_id=source_id)
    else:  # pragma: no cover - guarded by register()
        raise UsageError(f"不支持的文件类型：{suffix}")

    queue = review_mod.build_queue(
        result.blocks,
        source_id=source_id,
        source_file=stored.name,
        page_images=result.report.get("page_images") or {},
    )
    save_result(ws, result, queue)
    return {
        "source_id": source_id,
        "stored_as": str(stored.relative_to(ws.root)),
        "report": result.to_dict(),
        "review": {"queue": f"review/{source_id}.json",
                   "pending": queue["pending"],
                   "flagged": queue["summary"]["flagged_spans"]},
        "ok": not result.errors or bool(result.blocks),
        "errors": result.errors,
    }


def save_result(ws, result: SourceResult, queue: dict) -> None:
    blocks_path = ws.blocks_dir / f"{result.source_id}.jsonl"
    write_jsonl(blocks_path, [b.to_dict() for b in result.blocks])
    review_mod.save_queue(ws.review_dir / f"{result.source_id}.json",
                          {**queue, "saved_at": now_iso()})
    index_path = ws.blocks_dir / "index.json"
    index = read_json(index_path, {"schema": "jfa.block_index/1", "sources": []})
    entry = {
        "source_id": result.source_id,
        "file": result.path.name,
        "kind": result.kind,
        "stored_path": str(Path(result.path).relative_to(ws.root)),
        "blocks": len(result.blocks),
        "needs_review": sum(1 for b in result.blocks
                            if b.check_status in {B.CHECK_REVIEW, B.CHECK_UNVERIFIED}),
        "ingested_at": now_iso(),
        "report": result.report,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    sources = [s for s in index.get("sources", []) if s.get("source_id") != result.source_id]
    sources.append(entry)
    index["sources"] = sources
    index["updated_at"] = now_iso()
    write_json(index_path, index)


def load_blocks(ws, source_id: str, *, apply_corrections: bool = True) -> list[B.Block]:
    path = ws.blocks_dir / f"{source_id}.jsonl"
    if not path.exists():
        raise NotFoundError(f"未找到该来源的解析结果：{source_id}",
                            hint="运行 `jfa ingest <文件>` 或 `jfa sources` 查看已解析来源。")
    blocks = [B.Block.from_dict(row) for row in read_jsonl(path)]
    if apply_corrections:
        queue_path = ws.review_dir / f"{source_id}.json"
        if queue_path.exists():
            queue = read_json(queue_path)
            blocks, _ = review_mod.apply_corrections_to_blocks(blocks, queue)
    return [b for b in blocks if b.text.strip()]


def all_blocks(ws, *, apply_corrections: bool = True) -> tuple[list[B.Block], list[str]]:
    """Every block, kept in source order then page/order.

    Blocks from different files are never interleaved: an employment entry is a
    run of consecutive blocks, so mixing a DOCX and a PDF line by line would
    shred the structure. Sources are concatenated, not merged.
    """
    index = read_json(ws.blocks_dir / "index.json", {"sources": []})
    sources = index.get("sources", [])
    blocks: list[B.Block] = []
    missing: list[str] = []
    for position, entry in enumerate(sources):
        sid = entry.get("source_id")
        try:
            source_blocks = load_blocks(ws, sid, apply_corrections=apply_corrections)
        except NotFoundError:
            missing.append(sid)
            continue
        source_blocks.sort(key=lambda b: (b.page if b.page is not None else 0, b.order))
        blocks.extend(source_blocks)
    return blocks, missing


def sources(ws) -> list[dict]:
    index = read_json(ws.blocks_dir / "index.json", {"sources": []})
    return index.get("sources", [])

# -*- coding: utf-8 -*-
"""Plain-text resume export (searchable, greppable, no layout)."""

from __future__ import annotations

from pathlib import Path

from ..rewrite import render_text


def write_text(doc_model: dict, out_path: Path, *, include_pending: bool = False) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = render_text(doc_model, include_pending=include_pending)
    if not body.strip():
        from ..errors import ExportError

        raise ExportError("简历纯文本为空，拒绝写出空文件。")
    out_path.write_text(body, encoding="utf-8", newline="\n")
    return {"path": str(out_path), "chars": len(body), "lines": body.count("\n")}

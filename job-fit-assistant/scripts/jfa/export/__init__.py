# -*- coding: utf-8 -*-
"""Export orchestration: DOCX + PDF + TXT + reports, then verify everything."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import facts as F
from .. import reports as reports_mod
from .. import rewrite as RW
from ..errors import ExportError, ValidationError
from ..util import (
    now_iso,
    safe_component,
    sha256_file,
    stamp,
    unique_path,
    write_json,
)
from . import docx_out, pdf_out, text_out, verify as verify_mod

SCHEMA_MANIFEST = "jfa.export_manifest/1"


def export_bundle(
    ws,
    job: dict,
    *,
    plan: dict,
    mapping: dict | None = None,
    archive: dict | None = None,
    screening: dict | None = None,
    version: int = 1,
    style_path: Path | None = None,
    photo_path: Path | None = None,
    max_pages: int | None = None,
    min_font_pt: float | None = None,
    pdf_backends: list[str] | None = None,
    include_pending: bool = False,
    expected_min_chars: int = 120,
) -> dict:
    from ..jobs import export_allowed

    allowed, why = export_allowed(job)
    if not allowed:
        raise ExportError("职位身份未确认，已阻止导出。", hint=why,
                          detail={"identity": job.get("identity", {})})

    archive = archive or ws.load_facts()
    cfg = ws.load_config()
    export_cfg = cfg.get("export") or {}
    max_pages = int(max_pages or export_cfg.get("max_pages") or 2)
    style = docx_out.load_style(style_path or _default_style_path())
    min_font_pt = float(min_font_pt or export_cfg.get("min_font_pt") or style.get("min_body_pt", 9.5))

    # ---- gate: facts first, then prose ---------------------------------
    plan_report = RW.require_valid(plan, archive, job=job, mapping=mapping)
    doc_model = RW.build_resume_doc(archive, plan, job=job, include_pending=include_pending)
    trace_issues = RW.check_traceability(doc_model, archive)
    blocking = [i for i in trace_issues if i.get("severity") == "block"]
    if blocking:
        raise ValidationError(
            "简历内容无法完全追溯到已确认事实，已阻止导出。",
            detail={"issues": blocking[:10]},
        )
    expected_text = RW.render_text(doc_model, include_pending=include_pending)
    if len(expected_text.strip()) < expected_min_chars:
        raise ExportError(
            f"简历正文只有 {len(expected_text.strip())} 个字符，内容不足以生成有效简历。",
            hint="先确认事实档案、映射表与修改方案，再重新导出；不要生成空壳文件。",
        )

    # ---- output location, never overwriting ----------------------------
    out_dir = ws.out_version_dir(job["job_id"], version)
    stem = _stem(job, version)
    docx_path = unique_path(out_dir, stem, ".docx")
    stem = docx_path.stem
    pdf_path = out_dir / f"{stem}.pdf"
    txt_path = out_dir / f"{stem}.txt"
    render_dir = out_dir / f"{stem}-pages"

    fit_attempts: list[dict] = []
    render_info: dict[str, Any] = {}
    pdf_info: dict[str, Any] = {"status": "not_attempted"}
    backend_available = any(b.get("available") for b in pdf_out.detect_backends().values())

    body_candidates = _font_candidates(style, min_font_pt)
    chosen_pt = body_candidates[0]
    for body_pt in body_candidates:
        render_info = docx_out.render(doc_model, style, docx_path,
                                      photo_path=photo_path, body_pt=body_pt)
        if not backend_available:
            fit_attempts.append({"body_pt": body_pt, "pages": None,
                                 "note": "无 PDF 后端，无法测量页数"})
            chosen_pt = body_pt
            break
        try:
            pdf_info = pdf_out.convert(
                docx_path, out_dir, backends=pdf_backends,
                doc_model=doc_model, style=style, photo_path=photo_path,
                body_pt=body_pt,
            )
        except ExportError as exc:
            fit_attempts.append({"body_pt": body_pt, "pages": None,
                                 "error": exc.message})
            pdf_info = {"status": "failed", "error": exc.message,
                        "hint": exc.hint, "detail": exc.detail}
            chosen_pt = body_pt
            break
        pages = pdf_info.get("pages") or 0
        fit_attempts.append({"body_pt": body_pt, "pages": pages})
        chosen_pt = body_pt
        if pages and pages <= max_pages:
            break

    if chosen_pt < min_font_pt - 1e-6:
        raise ExportError(
            f"排版需要低于 {min_font_pt}pt 的字号，已拒绝。",
            hint="请压缩内容或提高导出页数上限；不通过过小字号强行压页。",
        )

    pdf_ok = Path(pdf_path).exists() and pdf_info.get("status") != "failed"
    if not pdf_ok:
        # never leave a zero-byte .pdf behind
        if Path(pdf_path).exists() and Path(pdf_path).stat().st_size < 1024:
            Path(pdf_path).unlink(missing_ok=True)

    txt_info = text_out.write_text(doc_model, txt_path, include_pending=include_pending)

    # ---- reports --------------------------------------------------------
    diff_md = reports_mod.render_change_diff(plan, job=job, mapping=mapping)
    match_md = reports_mod.render_match_report(mapping or {}, job=job,
                                               screening=screening, archive=archive)
    greeting = reports_mod.draft_greeting(job, mapping, archive, plan=plan)
    greeting_md = reports_mod.render_greeting(greeting, job)

    pages_dir = None
    if pdf_ok:
        pages_dir = pdf_out.render_pages(pdf_path, render_dir, dpi=110)

    verification = None
    if pdf_ok:
        verification = verify_mod.verify_export(
            pdf_path=pdf_path,
            docx_path=docx_path,
            doc_model=doc_model,
            render_dir=render_dir,
            max_pages=max_pages,
            min_font_pt=min_font_pt,
            expected_text=expected_text,
        )

    fact_md = reports_mod.render_fact_check(archive, doc=doc_model, plan_report=plan_report)
    ocr_log_path = ws.review_dir / "corrections.jsonl"
    if ocr_log_path.exists():
        from ..util import read_jsonl

        fact_md += "\n" + reports_mod.render_fact_check(
            archive, ocr_review=read_jsonl(ocr_log_path)[-30:]
        ).split("## OCR 核对记录", 1)[-1]

    files = {
        "resume_docx": docx_path,
        "resume_pdf": pdf_path if pdf_ok else None,
        "resume_txt": txt_path,
        "change_diff_md": out_dir / f"{stem}-修改对照.md",
        "match_report_md": out_dir / f"{stem}-匹配与缺口.md",
        "fact_check_md": out_dir / f"{stem}-事实与待确认.md",
        "greeting_md": out_dir / f"{stem}-打招呼草稿.md",
        "resume_doc_json": out_dir / f"{stem}-resume-doc.json",
        "verification_json": out_dir / f"{stem}-校验结果.json",
        "manifest_json": out_dir / "manifest.json",
    }
    _write(files["change_diff_md"], diff_md)
    _write(files["match_report_md"], match_md)
    _write(files["fact_check_md"], fact_md)
    _write(files["greeting_md"], greeting_md)
    write_json(files["resume_doc_json"], doc_model)
    if verification:
        write_json(files["verification_json"], verification)

    verification_ok = bool(verification and verification["ok"])
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "job_id": job["job_id"],
        "company": job.get("company"),
        "title": job.get("title"),
        "version": version,
        "exported_at": now_iso(),
        "style": style.get("name"),
        "body_pt": chosen_pt,
        "min_font_pt": min_font_pt,
        "max_pages": max_pages,
        "fit_attempts": fit_attempts,
        "pdf_backend": pdf_info.get("backend") if pdf_ok else None,
        "include_pending": include_pending,
        "outputs": {},
        "identity": job.get("identity", {}).get("status", ""),
        "identity_caveat": why,
        "verification": {
            "available": verification is not None,
            "ok": verification_ok,
            "failed": len((verification or {}).get("failures") or []),
            "warned": len((verification or {}).get("warnings") or []),
        },
        "plan_stats": plan_report.get("stats"),
        "pending_items": len(doc_model.get("pending_items") or []),
        "greeting": {"source": greeting.get("source"),
                     "verification_status": greeting.get("verification_status")},
        "ready": RW.readiness(doc_model, plan_report,
                              {"coverage": (mapping or {}).get("coverage") if mapping else None},
                              job),
    }
    for key, path in files.items():
        if path and Path(path).exists():
            manifest["outputs"][key] = {
                "path": str(Path(path).relative_to(ws.root)),
                "bytes": Path(path).stat().st_size,
                "sha256": sha256_file(path)[:16],
            }
    write_json(files["manifest_json"], manifest)
    manifest["outputs"]["manifest_json"] = {
        "path": str(files["manifest_json"].relative_to(ws.root)),
        "bytes": files["manifest_json"].stat().st_size,
    }

    ok = pdf_ok and verification_ok
    summary = {
        "ok": ok,
        "docx": str(docx_path),
        "pdf": str(pdf_path) if pdf_ok else None,
        "txt": str(txt_path),
        "docx_url": f"file:///{docx_path.as_posix()}" if docx_path.exists() else None,
        "backend": manifest["pdf_backend"],
        "pages": (pdf_info or {}).get("pages"),
        "body_pt": chosen_pt,
        "fit_attempts": fit_attempts,
        "verification": manifest["verification"],
        "verification_report": verification,
        "pending_items": manifest["pending_items"],
        "readiness": manifest["ready"],
        "pdf_error": None if pdf_ok else (pdf_info.get("error") or "PDF 未生成"),
        "pdf_hint": None if pdf_ok else (pdf_info.get("hint") or ""),
        "manifest": str(files["manifest_json"]),
    }
    return summary


def _stem(job: dict, version: int) -> str:
    company = safe_component(job.get("company", ""), max_len=24, fallback="company")
    title = safe_component(job.get("title", ""), max_len=28, fallback="role")
    return f"{company}-{title}-v{version}-{stamp()}"


def _font_candidates(style: dict, min_font_pt: float) -> list[float]:
    base = float((style.get("sizes") or {}).get("body", 10))
    candidates = [base]
    value = base
    while value - 0.5 >= min_font_pt - 1e-6 and len(candidates) < 4:
        value = round(value - 0.5, 2)
        candidates.append(value)
    return candidates


def _default_style_path() -> Path | None:
    for candidate in (
        Path(__file__).resolve().parents[2] / "assets" / "templates" / "resume-style.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")

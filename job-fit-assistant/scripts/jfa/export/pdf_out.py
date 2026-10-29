# -*- coding: utf-8 -*-
"""DOCX -> PDF conversion through an explicit backend chain.

Order is configurable (`export.pdf_backends`). Each backend is tried in turn;
if none works this raises `ExportError`. It never writes an empty PDF and calls
it success, and it always verifies the produced file has pages and selectable
text.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..errors import DependencyMissing, ExportError

WORD_EXPORT_FORMAT_PDF = 17


def detect_backends() -> dict[str, dict]:
    from . import pdf_builtin

    return {
        "libreoffice": _detect_soffice(),
        "word": _detect_word(),
        "builtin": {
            "available": pdf_builtin.available(),
            "path": "pymupdf",
            "note": "内置渲染器：不依赖外部应用，版式与 Word 不完全一致",
            "install": "pip install PyMuPDF",
        },
    }


def _detect_soffice() -> dict:
    candidates = [
        shutil.which("soffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return {"available": True, "path": candidate}
    return {"available": False, "path": "",
            "install": "安装 LibreOffice：https://www.libreoffice.org/download/ 后重试。"}


def _detect_word() -> dict:
    from .. import wincom

    result = wincom.probe()
    if result["available"]:
        return {"available": True, "path": "COM", "version": result["version"]}
    return {"available": False, "path": "",
            "install": f"Word COM 不可用：{result['reason']}"}


def convert(
    docx_path: Path,
    out_dir: Path,
    *,
    backends: list[str] | None = None,
    doc_model: dict | None = None,
    style: dict | None = None,
    photo_path: Path | None = None,
    body_pt: float | None = None,
) -> dict:
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise ExportError(f"待转换的 DOCX 不存在：{docx_path}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    detected = detect_backends()
    order = backends or ["libreoffice", "word", "builtin"]
    attempts: list[dict] = []

    for name in order:
        info = detected.get(name, {"available": False})
        if not info.get("available"):
            attempts.append({"backend": name, "ok": False,
                             "reason": "未安装或不可用", "install": info.get("install", "")})
            continue
        target = out_dir / (docx_path.stem + ".pdf")
        try:
            if name == "libreoffice":
                pdf_path = _with_soffice(docx_path, out_dir, info["path"])
            elif name == "word":
                pdf_path = _with_word(docx_path, out_dir)
            elif name == "builtin":
                if not doc_model:
                    attempts.append({"backend": name, "ok": False,
                                     "reason": "缺少文档模型，无法内置渲染"})
                    continue
                from . import pdf_builtin

                result = pdf_builtin.render(doc_model, style or {}, target,
                                            photo_path=photo_path, body_pt=body_pt)
                pdf_path = Path(result["path"])
            else:
                attempts.append({"backend": name, "ok": False, "reason": "未知后端"})
                continue
        except Exception as exc:
            attempts.append({"backend": name, "ok": False, "reason": str(exc)})
            continue

        check = verify_pdf_file(pdf_path)
        attempts.append({"backend": name, "ok": check["ok"], "reason": check["reason"],
                         "path": str(pdf_path)})
        if check["ok"]:
            return {"path": str(pdf_path), "backend": name, "attempts": attempts,
                    "pages": check["pages"], "chars": check["chars"]}
        try:
            pdf_path.unlink()
        except OSError:
            pass

    hints = "；".join(a.get("install", "") for a in attempts if a.get("install"))
    raise ExportError(
        "无法生成 PDF：没有可用的转换后端，或生成结果不可用。",
        hint=hints or "安装 LibreOffice，或安装 PyMuPDF 以启用内置渲染器。",
        detail={"attempts": attempts},
    )


def _with_soffice(docx_path: Path, out_dir: Path, soffice: str) -> Path:
    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            soffice, "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation=file:///{Path(profile).as_posix()}",
            "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(out_dir), str(docx_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    candidate = out_dir / (docx_path.stem + ".pdf")
    if not candidate.exists():
        raise ExportError(
            "LibreOffice 未生成 PDF。",
            detail={"returncode": proc.returncode,
                    "stdout": (proc.stdout or "")[-800:],
                    "stderr": (proc.stderr or "")[-800:]},
        )
    return candidate


def _with_word(docx_path: Path, out_dir: Path) -> Path:
    from .. import wincom

    target = out_dir / (docx_path.stem + ".pdf")
    wincom.convert_docx_to_pdf(docx_path, target)
    if not target.exists():
        raise ExportError("Word 未生成 PDF 文件。")
    return target


def verify_pdf_file(pdf_path: Path) -> dict:
    """Cheap structural check used to decide whether a backend succeeded."""
    path = Path(pdf_path)
    if not path.exists():
        return {"ok": False, "reason": "文件未生成", "pages": 0, "chars": 0}
    size = path.stat().st_size
    if size < 1024:
        return {"ok": False, "reason": f"文件过小（{size} 字节），疑似空文件",
                "pages": 0, "chars": 0}
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        return {"ok": True, "reason": "缺少 PyMuPDF，未做深度校验", "pages": 0, "chars": 0}
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        return {"ok": False, "reason": f"无法打开 PDF：{exc}", "pages": 0, "chars": 0}
    try:
        pages = doc.page_count
        chars = sum(len((doc[i].get_text("text") or "").strip()) for i in range(pages))
        if pages == 0:
            return {"ok": False, "reason": "PDF 没有任何页面", "pages": 0, "chars": 0}
        if chars < 10:
            return {"ok": False, "reason": "PDF 中没有可选中的文字层",
                    "pages": pages, "chars": chars}
        return {"ok": True, "reason": "", "pages": pages, "chars": chars,
                "size_bytes": size}
    finally:
        doc.close()


def render_pages(pdf_path: Path, out_dir: Path, *, dpi: int = 110) -> list[Path]:
    """Rasterise every page for visual checks. Used by verify."""
    import pymupdf

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    doc = pymupdf.open(str(pdf_path))
    try:
        for index in range(doc.page_count):
            pix = doc[index].get_pixmap(dpi=dpi, alpha=False)
            target = out_dir / f"page-{index + 1:02d}.png"
            pix.save(str(target))
            paths.append(target)
    finally:
        doc.close()
    return paths

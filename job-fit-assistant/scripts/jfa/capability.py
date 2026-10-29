# -*- coding: utf-8 -*-
"""Startup capability check.

The check must never flatter the environment. Anything not positively verified
is reported as unavailable/unknown, because the host agent will tell the user
what this run can and cannot do.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .util import now_iso
from .workspace import CORE_VERSION, Workspace

STATUS_OK = "available"
STATUS_MISSING = "unavailable"
STATUS_UNKNOWN = "unknown"

#: DOCX -> PDF backends, best fidelity first. "builtin" needs only PyMuPDF.
DEFAULT_BACKENDS = ["libreoffice", "word", "builtin"]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _module_version(name: str) -> str:
    try:
        mod = __import__(name)
    except Exception:
        return ""
    return str(getattr(mod, "__version__", "") or "")


def _probe_soffice() -> dict:
    candidates = [
        shutil.which("soffice"),
        shutil.which("soffice.exe"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            version = ""
            try:
                out = subprocess.run(
                    [cand, "--version"], capture_output=True, text=True, timeout=20
                )
                version = (out.stdout or out.stderr or "").strip().splitlines()[0][:80]
            except Exception:
                pass
            return {"status": STATUS_OK, "path": cand, "version": version,
                    "converts": ["docx->pdf"], "note": ""}
    return {
        "status": STATUS_MISSING,
        "path": "",
        "version": "",
        "converts": [],
        "note": "未找到 soffice；PDF 导出将改用其他后端或如实失败。",
    }


def _probe_word() -> dict:
    """Run the COM check in an isolated child process with a hard timeout."""
    from . import wincom

    result = wincom.probe()
    if result["available"]:
        return {"status": STATUS_OK, "version": result["version"],
                "note": "Word COM 可用。"}
    return {"status": STATUS_MISSING, "version": "", "note": result["reason"]}


def probe_ocr(lang: str = "ch") -> dict:
    """Report which OCR backends are actually importable. No guessing."""
    paddle_framework = _module_available("paddle")
    paddleocr = _module_available("paddleocr")
    rapid = _module_available("rapidocr_onnxruntime")
    tesseract = shutil.which("tesseract")
    backends = []
    if paddleocr and paddle_framework:
        backends.append({
            "name": "paddleocr",
            "type": "local",
            "version": _module_version("paddleocr"),
            "framework_version": _module_version("paddle"),
            "structure_v3": True,
            "note": "支持 PP-OCRv5 与 PP-StructureV3（复杂版式）。",
        })
    if rapid:
        backends.append({
            "name": "rapidocr",
            "type": "local",
            "version": _module_version("rapidocr_onnxruntime"),
            "structure_v3": False,
            "note": "同一 PP-OCR 模型的 ONNX 版本；无版面结构分析。",
        })
    if tesseract:
        try:
            out = subprocess.run([tesseract, "--version"], capture_output=True,
                                 text=True, timeout=20)
            ver = (out.stdout or "").splitlines()[0][:80]
        except Exception:
            ver = ""
        backends.append({
            "name": "tesseract",
            "type": "local-external",
            "version": ver,
            "structure_v3": False,
            "note": "需要系统 tessdata 中文包，否则中文识别效果差。",
        })
    status = STATUS_OK if backends else STATUS_MISSING
    return {
        "status": status,
        "lang": lang,
        "backends": backends,
        "paddle_framework": paddle_framework,
        "note": "" if backends else (
            "未检测到任何 OCR 后端：扫描件 PDF 与图片将无法识别文字，"
            "程序会如实报告失败，不会输出伪造文字。"
        ),
        "install_hint": (
            "pip install -r scripts/requirements-ocr.txt  "
            "(paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/ ; "
            "paddleocr==3.4.1)"
        ),
    }


def probe_docx_engine() -> dict:
    ok = _module_available("docx")
    return {
        "status": STATUS_OK if ok else STATUS_MISSING,
        "version": _module_version("docx"),
        "note": "" if ok else "未安装 python-docx，无法生成 DOCX。",
        "install_hint": "pip install -r scripts/requirements.txt",
    }


def probe_pdf_reader() -> dict:
    ok = _module_available("pymupdf") or _module_available("fitz")
    return {
        "status": STATUS_OK if ok else STATUS_MISSING,
        "version": _module_version("pymupdf") or _module_version("fitz"),
        "note": "" if ok else "未安装 PyMuPDF，无法逐页检测 PDF 与渲染校验页。",
        "install_hint": "pip install -r scripts/requirements.txt",
    }


def probe_image() -> dict:
    ok = _module_available("PIL")
    return {
        "status": STATUS_OK if ok else STATUS_MISSING,
        "version": _module_version("PIL"),
        "note": "" if ok else "未安装 Pillow，无法读取 PNG/JPG 或插入照片。",
        "install_hint": "pip install -r scripts/requirements.txt",
    }


def probe_file_access(ws: Workspace) -> dict:
    """Actually attempt a write, not just an `os.access` guess."""
    writable = False
    detail = ""
    try:
        ws.root.mkdir(parents=True, exist_ok=True)
        probe = ws.root / ".jfa-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except Exception as exc:
        detail = str(exc)
    readable = os.access(ws.root, os.R_OK)
    return {
        "status": STATUS_OK if writable else STATUS_MISSING,
        "workspace": str(ws.root),
        "readable": readable,
        "writable": writable,
        "note": detail,
    }


def probe_browser(ws: Workspace) -> dict:
    """Browser capability is *declared by the host*, never invented here.

    A skill cannot know which browsing tool a host exposes. The host agent must
    record what it actually has via `jfa host declare`; until then this is
    unknown, and the workflow falls back to user-pasted job text.
    """
    cfg = ws.load_config() if ws.exists() else {"host": {}}
    host = (cfg.get("host") or {})
    if not host.get("declared"):
        return {
            "status": STATUS_UNKNOWN,
            "declared": False,
            "kind": "",
            "tool_names": [],
            "login_scope": "",
            "note": (
                "宿主未声明浏览器能力。本程序不探测、不猜测浏览器工具名；"
                "请宿主 agent 依据自身真实工具清单运行 `jfa host declare`，"
                "或改用用户粘贴的职位文本。"
            ),
            "fallback": "user_pasted_job_text",
        }
    return {
        "status": STATUS_OK,
        "declared": True,
        "kind": host.get("browser_kind", ""),
        "tool_names": host.get("browser_tool_names", []),
        "login_scope": host.get("login_scope", ""),
        "declared_at": host.get("declared_at", ""),
        "note": host.get("notes", ""),
        "fallback": "user_pasted_job_text",
    }


def probe_pdf_export(ws: Workspace) -> dict:
    from .export import pdf_builtin

    cfg = ws.load_config() if ws.exists() else {"export": {"pdf_backends": DEFAULT_BACKENDS}}
    order = (cfg.get("export") or {}).get("pdf_backends", DEFAULT_BACKENDS)
    soffice = _probe_soffice()
    word = _probe_word()
    builtin = {
        "status": STATUS_OK if pdf_builtin.available() else STATUS_MISSING,
        "note": "内置渲染器：不依赖外部应用，版式与 Word 不完全一致，作为兜底。" if pdf_builtin.available()
        else "缺少 PyMuPDF，内置渲染器不可用。",
    }
    chain = []
    for name in order:
        if name == "libreoffice" and soffice["status"] == STATUS_OK:
            chain.append({"backend": "libreoffice", "path": soffice["path"]})
        if name == "word" and word["status"] == STATUS_OK:
            chain.append({"backend": "word", "path": "COM"})
        if name == "builtin" and builtin["status"] == STATUS_OK:
            chain.append({"backend": "builtin", "path": "pymupdf"})
    return {
        "status": STATUS_OK if chain else STATUS_MISSING,
        "chain": chain,
        "libreoffice": soffice,
        "word": word,
        "builtin": builtin,
        "note": "" if chain else (
            "没有可用的 PDF 后端。仍可导出 DOCX/TXT，但 PDF 会报告失败，"
            "不会生成空文件冒充成功。"
        ),
        "install_hint": "安装 LibreOffice（版式最接近 Word）或 pip install PyMuPDF（内置兜底）。",
    }


def probe_location() -> dict:
    """Where the text-handling code physically executes."""
    return {
        "status": STATUS_OK,
        "execution_location": "local",
        "host": platform.node(),
        "platform": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "core_version": CORE_VERSION,
        "note": "解析、校验、计算、文档生成全部在本机进程内完成，不调用外部模型 API。",
    }


def probe_pdf_render() -> dict:
    ok = _module_available("pymupdf") or _module_available("fitz")
    return {
        "status": STATUS_OK if ok else STATUS_MISSING,
        "version": _module_version("pymupdf") or _module_version("fitz"),
        "note": "用于导出后逐页渲染检查（截断/空白页/乱码）。" if ok
        else "缺少 PyMuPDF，导出后无法做逐页渲染检查。",
    }


def run_probe(ws: Workspace, *, lang: str = "ch") -> dict:
    caps: dict[str, Any] = {
        "schema": "jfa.capability/1",
        "generated_at": now_iso(),
        "location": probe_location(),
        "file_access": probe_file_access(ws),
        "docx_engine": probe_docx_engine(),
        "pdf_reader": probe_pdf_reader(),
        "image": probe_image(),
        "pdf_render": probe_pdf_render(),
        "ocr": probe_ocr(lang),
        "pdf_export": probe_pdf_export(ws),
        "browser": probe_browser(ws),
    }
    caps["summary"] = summarize(caps)
    caps["blocking"] = [k for k, v in caps["summary"].items() if v == STATUS_MISSING]
    return caps


_REQUIRED = ("file_access", "docx_engine", "pdf_reader")


def summarize(caps: dict) -> dict:
    """One short status per user-facing capability."""
    ocr = caps["ocr"]
    export = caps["pdf_export"]
    return {
        "本地执行": caps["location"]["status"],
        "文件读写": caps["file_access"]["status"],
        "DOCX 解析/生成": caps["docx_engine"]["status"],
        "PDF 逐页检测": caps["pdf_reader"]["status"],
        "图片读取": caps["image"]["status"],
        "PDF 页面渲染校验": caps["pdf_render"]["status"],
        "OCR 文字识别": ocr["status"],
        "PDF 导出": export["status"],
        "浏览器": caps["browser"]["status"],
    }


def render_report(caps: dict) -> str:
    """Human-readable table for the host to paste into the chat."""
    lines = ["能力检查（本地实测，未通过即标为不可用）：", ""]
    labels = {
        "本地执行": ("执行位置", "本地进程内；解析/校验/生成均不调用外部模型 API"),
        "文件读写": ("文件读写", str(caps["file_access"].get("workspace", ""))),
        "DOCX 解析/生成": ("DOCX 解析/生成", f"python-docx {caps['docx_engine'].get('version','')}"),
        "PDF 逐页检测": ("PDF 逐页检测", f"PyMuPDF {caps['pdf_reader'].get('version','')}"),
        "图片读取": ("图片读取", f"Pillow {caps['image'].get('version','')}"),
        "PDF 页面渲染校验": ("PDF 页面渲染校验", "导出后逐页渲染 + 重抽取文字"),
        "OCR 文字识别": ("OCR 文字识别", _ocr_detail(caps["ocr"])),
        "PDF 导出": ("PDF 导出", _export_detail(caps["pdf_export"])),
        "浏览器": ("浏览器", _browser_detail(caps["browser"])),
    }
    for key, status in caps["summary"].items():
        label, detail = labels.get(key, (key, ""))
        mark = {STATUS_OK: "可用", STATUS_MISSING: "不可用", STATUS_UNKNOWN: "未声明"}[status]
        lines.append(f"- {label}：{mark}" + (f"　{detail}" if detail else ""))
    browser = caps["browser"]
    if browser["status"] == STATUS_UNKNOWN:
        lines.append("")
        lines.append(
            "注意：浏览器能力未声明。本 Skill 不会虚构通用浏览器工具名；"
            "没有浏览器时，请让用户直接粘贴职位文本（`jfa job add --text`）。"
        )
    ocr = caps["ocr"]
    if ocr["status"] == STATUS_MISSING:
        lines.append("")
        lines.append(f"OCR 安装指引：{ocr['install_hint']}")
    export = caps["pdf_export"]
    if export["status"] == STATUS_MISSING:
        lines.append("")
        lines.append(f"PDF 导出安装指引：{export['install_hint']}")
    return "\n".join(lines)


def _ocr_detail(ocr: dict) -> str:
    if ocr["status"] != STATUS_OK:
        return "未检测到 OCR 后端；扫描件/图片会如实报告失败"
    names = ", ".join(b["name"] for b in ocr["backends"])
    return f"后端 {names}；语言 {ocr['lang']}"


def _export_detail(export: dict) -> str:
    if export["status"] != STATUS_OK:
        return "无可用 DOCX→PDF 后端"
    return " → ".join(step["backend"] for step in export["chain"])


def _browser_detail(browser: dict) -> str:
    if browser["status"] == STATUS_UNKNOWN:
        return "宿主未声明，改用粘贴文本兜底"
    kind = browser.get("kind") or "已声明"
    scope = browser.get("login_scope") or "登录状态未说明"
    return f"{kind}；{scope}"
